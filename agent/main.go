package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base32"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	protocolVersion = 1
	defaultStateDir = "/var/lib/wolnut-agent"
	defaultListen   = "0.0.0.0:8184"
)

var version = "dev"

type persistedState struct {
	AgentID             string               `json:"agent_id"`
	BootstrapCertPEM    string               `json:"bootstrap_cert_pem"`
	BootstrapKeyPEM     string               `json:"bootstrap_key_pem"`
	ControllerCAPEM     string               `json:"controller_ca_pem,omitempty"`
	ControllerCertPEM   string               `json:"controller_cert_pem,omitempty"`
	ServerCertPEM       string               `json:"server_cert_pem,omitempty"`
	ServerKeyPEM        string               `json:"server_key_pem,omitempty"`
	PendingServerKeyPEM string               `json:"pending_server_key_pem,omitempty"`
	PairingCodeHash     string               `json:"pairing_code_hash,omitempty"`
	PairingExpiresAt    int64                `json:"pairing_expires_at,omitempty"`
	PairingFailures     int                  `json:"pairing_failures,omitempty"`
	CompletionTokenHash string               `json:"completion_token_hash,omitempty"`
	CompletionExpiresAt int64                `json:"completion_expires_at,omitempty"`
	Processed           map[string]processed `json:"processed,omitempty"`
}

type processed struct {
	AcceptedAt int64 `json:"accepted_at"`
}

type store struct {
	dir string
	mu  sync.Mutex
}

func (s *store) path() string { return filepath.Join(s.dir, "state.json") }

func (s *store) load() (*persistedState, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.loadUnlocked()
}

func (s *store) loadUnlocked() (*persistedState, error) {
	data, err := os.ReadFile(s.path())
	if err != nil {
		return nil, err
	}
	var state persistedState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, err
	}
	if state.Processed == nil {
		state.Processed = map[string]processed{}
	}
	return &state, nil
}

func (s *store) save(state *persistedState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.saveUnlocked(state)
}

func (s *store) saveUnlocked(state *persistedState) error {
	if err := os.MkdirAll(s.dir, 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Chmod(tmp, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, s.path())
}

func (s *store) initialize() (*persistedState, error) {
	state, err := s.load()
	if err == nil {
		return state, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	idBytes := make([]byte, 16)
	if _, err := rand.Read(idBytes); err != nil {
		return nil, err
	}
	agentID := hex.EncodeToString(idBytes)
	certPEM, keyPEM, err := createSelfSignedCertificate(agentID)
	if err != nil {
		return nil, err
	}
	state = &persistedState{
		AgentID:          agentID,
		BootstrapCertPEM: certPEM,
		BootstrapKeyPEM:  keyPEM,
		Processed:        map[string]processed{},
	}
	return state, s.save(state)
}

func createSelfSignedCertificate(agentID string) (string, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", "", err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return "", "", err
	}
	identity, _ := url.Parse("urn:wolnut:agent:" + agentID)
	now := time.Now()
	tmpl := x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "wolnut-agent-" + agentID},
		NotBefore:    now.Add(-5 * time.Minute),
		NotAfter:     now.AddDate(5, 0, 0),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		URIs:         []*url.URL{identity},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &key.PublicKey, key)
	if err != nil {
		return "", "", err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", "", err
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})),
		string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})), nil
}

func certificateFingerprint(certPEM string) (string, error) {
	block, _ := pem.Decode([]byte(certPEM))
	if block == nil || block.Type != "CERTIFICATE" {
		return "", errors.New("invalid certificate")
	}
	sum := sha256.Sum256(block.Bytes)
	parts := make([]string, len(sum))
	for i, value := range sum {
		parts[i] = fmt.Sprintf("%02X", value)
	}
	return strings.Join(parts, ":"), nil
}

func createCSR(agentID string) (string, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", "", err
	}
	identity, _ := url.Parse("urn:wolnut:agent:" + agentID)
	tmpl := x509.CertificateRequest{
		Subject: pkix.Name{CommonName: "wolnut-agent-" + agentID},
		URIs:    []*url.URL{identity},
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, &tmpl, key)
	if err != nil {
		return "", "", err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", "", err
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})),
		string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})), nil
}

type poweroffExecutor interface{ Poweroff() error }
type systemdPoweroff struct{}

func (systemdPoweroff) Poweroff() error {
	return exec.Command("/usr/bin/systemctl", "poweroff").Run()
}

type server struct {
	store         *store
	poweroff      poweroffExecutor
	shutdownDelay time.Duration
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/bootstrap/pair", s.handleBootstrapPair)
	mux.HandleFunc("/bootstrap/complete", s.handleBootstrapComplete)
	mux.HandleFunc("/v1/status", s.requireController(s.handleStatus))
	mux.HandleFunc("/v1/shutdown", s.requireController(s.handleShutdown))
	mux.HandleFunc("/v1/unpair", s.requireController(s.handleUnpair))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		mux.ServeHTTP(w, r)
	})
}

func (s *server) requireController(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		state, err := s.store.load()
		if err != nil || state.ControllerCAPEM == "" {
			writeError(w, http.StatusForbidden, "agent is not paired")
			return
		}
		if r.TLS == nil || len(r.TLS.VerifiedChains) == 0 {
			writeError(w, http.StatusUnauthorized, "valid controller certificate required")
			return
		}
		leaf := r.TLS.PeerCertificates[0]
		if !hasUsage(leaf, x509.ExtKeyUsageClientAuth) {
			writeError(w, http.StatusUnauthorized, "controller certificate has invalid usage")
			return
		}
		next(w, r)
	}
}

func hasUsage(cert *x509.Certificate, expected x509.ExtKeyUsage) bool {
	for _, usage := range cert.ExtKeyUsage {
		if usage == expected {
			return true
		}
	}
	return false
}

type pairRequest struct {
	Code           string `json:"code"`
	ControllerCA   string `json:"controller_ca"`
	ControllerCert string `json:"controller_cert"`
}

func (s *server) validatePairingCode(state *persistedState, code string) error {
	if state.ServerCertPEM != "" {
		return errors.New("agent is already paired")
	}
	if state.PairingFailures >= 5 || time.Now().Unix() > state.PairingExpiresAt {
		return errors.New("pairing code expired")
	}
	sum := sha256.Sum256([]byte(strings.TrimSpace(code)))
	if !strings.EqualFold(hex.EncodeToString(sum[:]), state.PairingCodeHash) {
		state.PairingFailures++
		_ = s.store.save(state)
		return errors.New("invalid pairing code")
	}
	return nil
}

func (s *server) handleBootstrapPair(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var request pairRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	state, err := s.store.load()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "state unavailable")
		return
	}
	if err := s.validatePairingCode(state, request.Code); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}
	ca, err := parseCertificate(request.ControllerCA)
	if err != nil || !ca.IsCA {
		writeError(w, http.StatusBadRequest, "invalid controller CA")
		return
	}
	controller, err := parseCertificate(request.ControllerCert)
	if err != nil || !hasUsage(controller, x509.ExtKeyUsageClientAuth) {
		writeError(w, http.StatusBadRequest, "invalid controller certificate")
		return
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if _, err := controller.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		writeError(w, http.StatusBadRequest, "controller certificate verification failed")
		return
	}
	csr, key, err := createCSR(state.AgentID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not create certificate request")
		return
	}
	state.ControllerCAPEM = request.ControllerCA
	state.ControllerCertPEM = request.ControllerCert
	state.PendingServerKeyPEM = key
	completionToken, completionHash, err := newSecret()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not create pairing session")
		return
	}
	state.PairingCodeHash = ""
	state.PairingExpiresAt = 0
	state.CompletionTokenHash = completionHash
	state.CompletionExpiresAt = time.Now().Add(2 * time.Minute).Unix()
	if err := s.store.save(state); err != nil {
		writeError(w, http.StatusInternalServerError, "could not save pairing state")
		return
	}
	hostname, _ := os.Hostname()
	writeJSON(w, http.StatusOK, map[string]any{
		"agent_id":         state.AgentID,
		"csr":              csr,
		"completion_token": completionToken,
		"hostname":         hostname,
		"version":          version,
	})
}

type completeRequest struct {
	CompletionToken string `json:"completion_token"`
	ServerCert      string `json:"server_cert"`
}

func validateCompletionToken(state *persistedState, token string) error {
	if state.CompletionTokenHash == "" || time.Now().Unix() > state.CompletionExpiresAt {
		return errors.New("pairing completion token expired")
	}
	sum := sha256.Sum256([]byte(strings.TrimSpace(token)))
	if !strings.EqualFold(hex.EncodeToString(sum[:]), state.CompletionTokenHash) {
		return errors.New("invalid pairing completion token")
	}
	return nil
}

func (s *server) handleBootstrapComplete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var request completeRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	state, err := s.store.load()
	if err != nil || state.PendingServerKeyPEM == "" {
		writeError(w, http.StatusConflict, "pairing has not been started")
		return
	}
	if err := validateCompletionToken(state, request.CompletionToken); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}
	cert, err := parseCertificate(request.ServerCert)
	ca, caErr := parseCertificate(state.ControllerCAPEM)
	if err != nil || caErr != nil || !hasUsage(cert, x509.ExtKeyUsageServerAuth) {
		writeError(w, http.StatusBadRequest, "invalid agent certificate")
		return
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if _, err := cert.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		writeError(w, http.StatusBadRequest, "agent certificate verification failed")
		return
	}
	if !certificateHasIdentity(cert, "urn:wolnut:agent:"+state.AgentID) {
		writeError(w, http.StatusBadRequest, "agent certificate identity mismatch")
		return
	}
	if _, err := tls.X509KeyPair([]byte(request.ServerCert), []byte(state.PendingServerKeyPEM)); err != nil {
		writeError(w, http.StatusBadRequest, "agent certificate does not match key")
		return
	}
	state.ServerCertPEM = request.ServerCert
	state.ServerKeyPEM = state.PendingServerKeyPEM
	state.PendingServerKeyPEM = ""
	state.PairingCodeHash = ""
	state.PairingExpiresAt = 0
	state.PairingFailures = 0
	state.CompletionTokenHash = ""
	state.CompletionExpiresAt = 0
	if err := s.store.save(state); err != nil {
		writeError(w, http.StatusInternalServerError, "could not complete pairing")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "paired", "agent_id": state.AgentID})
}

func certificateHasIdentity(cert *x509.Certificate, identity string) bool {
	for _, uri := range cert.URIs {
		if uri.String() == identity {
			return true
		}
	}
	return false
}

func parseCertificate(value string) (*x509.Certificate, error) {
	block, _ := pem.Decode([]byte(value))
	if block == nil || block.Type != "CERTIFICATE" {
		return nil, errors.New("invalid certificate PEM")
	}
	return x509.ParseCertificate(block.Bytes)
}

func (s *server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	state, _ := s.store.load()
	hostname, _ := os.Hostname()
	certificateExpiresAt := int64(0)
	if certificate, err := parseCertificate(state.ServerCertPEM); err == nil {
		certificateExpiresAt = certificate.NotAfter.Unix()
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"protocol_version":       protocolVersion,
		"agent_id":               state.AgentID,
		"hostname":               hostname,
		"version":                version,
		"paired":                 true,
		"certificate_expires_at": certificateExpiresAt,
	})
}

type shutdownRequest struct {
	CommandID      string `json:"command_id"`
	Source         string `json:"source"`
	RequestedAt    int64  `json:"requested_at"`
	ExpiresAt      int64  `json:"expires_at"`
	UPS            string `json:"ups,omitempty"`
	BatteryPercent *int   `json:"battery_percent,omitempty"`
	Threshold      *int   `json:"threshold_percent,omitempty"`
}

func validateShutdownRequest(request shutdownRequest, now time.Time) error {
	if strings.TrimSpace(request.CommandID) == "" {
		return errors.New("command_id is required")
	}
	if request.Source != "automatic" && request.Source != "manual" {
		return errors.New("invalid shutdown source")
	}
	if request.ExpiresAt <= now.Unix() || request.ExpiresAt > now.Add(5*time.Minute).Unix() {
		return errors.New("shutdown request is expired or too far in the future")
	}
	if request.RequestedAt > now.Add(2*time.Minute).Unix() || request.RequestedAt < now.Add(-5*time.Minute).Unix() {
		return errors.New("shutdown request time is invalid")
	}
	return nil
}

func (s *server) handleShutdown(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var request shutdownRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	if err := validateShutdownRequest(request, time.Now()); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	state, err := s.store.load()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "state unavailable")
		return
	}
	if _, duplicate := state.Processed[request.CommandID]; duplicate {
		writeJSON(w, http.StatusOK, map[string]any{"status": "accepted", "command_id": request.CommandID, "duplicate": true})
		return
	}
	state.Processed[request.CommandID] = processed{AcceptedAt: time.Now().Unix()}
	pruneProcessed(state.Processed, 100)
	if err := s.store.save(state); err != nil {
		writeError(w, http.StatusInternalServerError, "could not persist command")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"status": "accepted", "command_id": request.CommandID, "duplicate": false})
	go func() {
		time.Sleep(s.shutdownDelay)
		if err := s.poweroff.Poweroff(); err != nil {
			log.Printf("poweroff failed: %v", err)
		}
	}()
}

func pruneProcessed(values map[string]processed, limit int) {
	if len(values) <= limit {
		return
	}
	type item struct {
		id string
		at int64
	}
	items := make([]item, 0, len(values))
	for id, value := range values {
		items = append(items, item{id: id, at: value.AcceptedAt})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].at < items[j].at })
	for _, value := range items[:len(items)-limit] {
		delete(values, value.id)
	}
}

func (s *server) handleUnpair(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	state, err := s.store.load()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "state unavailable")
		return
	}
	state.ControllerCAPEM = ""
	state.ControllerCertPEM = ""
	state.ServerCertPEM = ""
	state.ServerKeyPEM = ""
	state.PendingServerKeyPEM = ""
	state.PairingCodeHash = ""
	state.PairingExpiresAt = 0
	state.PairingFailures = 0
	state.CompletionTokenHash = ""
	state.CompletionExpiresAt = 0
	state.Processed = map[string]processed{}
	if err := s.store.save(state); err != nil {
		writeError(w, http.StatusInternalServerError, "could not reset pairing")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "unpaired"})
}

func decodeJSON(w http.ResponseWriter, r *http.Request, target any) bool {
	defer r.Body.Close()
	decoder := json.NewDecoder(io.LimitReader(r.Body, 64*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request")
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func (s *server) tlsConfig() *tls.Config {
	return &tls.Config{
		MinVersion: tls.VersionTLS13,
		GetConfigForClient: func(*tls.ClientHelloInfo) (*tls.Config, error) {
			state, err := s.store.load()
			if err != nil {
				return nil, err
			}
			certPEM, keyPEM := state.BootstrapCertPEM, state.BootstrapKeyPEM
			config := &tls.Config{MinVersion: tls.VersionTLS13, ClientAuth: tls.RequestClientCert}
			if state.ServerCertPEM != "" {
				certPEM, keyPEM = state.ServerCertPEM, state.ServerKeyPEM
				pool := x509.NewCertPool()
				if !pool.AppendCertsFromPEM([]byte(state.ControllerCAPEM)) {
					return nil, errors.New("invalid controller CA")
				}
				config.ClientCAs = pool
				config.ClientAuth = tls.VerifyClientCertIfGiven
			}
			certificate, err := tls.X509KeyPair([]byte(certPEM), []byte(keyPEM))
			if err != nil {
				return nil, err
			}
			config.Certificates = []tls.Certificate{certificate}
			return config, nil
		},
	}
}

func pairingCode(stateStore *store) error {
	state, err := stateStore.initialize()
	if err != nil {
		return err
	}
	if state.ServerCertPEM != "" {
		return errors.New("agent is already paired; reset pairing first")
	}
	code, codeHash, err := newSecret()
	if err != nil {
		return err
	}
	state.ControllerCAPEM = ""
	state.ControllerCertPEM = ""
	state.PendingServerKeyPEM = ""
	state.CompletionTokenHash = ""
	state.CompletionExpiresAt = 0
	state.PairingCodeHash = codeHash
	state.PairingExpiresAt = time.Now().Add(10 * time.Minute).Unix()
	state.PairingFailures = 0
	if err := stateStore.save(state); err != nil {
		return err
	}
	fingerprint, err := certificateFingerprint(state.BootstrapCertPEM)
	if err != nil {
		return err
	}
	fmt.Printf("Pairing code: %s\n", code)
	fmt.Printf("Certificate fingerprint: %s\n", fingerprint)
	fmt.Println("Expires in 10 minutes")
	return nil
}

func newSecret() (string, string, error) {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return "", "", err
	}
	secret := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(random)
	sum := sha256.Sum256([]byte(secret))
	return secret, hex.EncodeToString(sum[:]), nil
}

func resetPairing(stateStore *store, confirmed bool) error {
	if !confirmed {
		return errors.New("reset-pairing requires --confirm")
	}
	state, err := stateStore.initialize()
	if err != nil {
		return err
	}
	state.ControllerCAPEM = ""
	state.ControllerCertPEM = ""
	state.ServerCertPEM = ""
	state.ServerKeyPEM = ""
	state.PendingServerKeyPEM = ""
	state.PairingCodeHash = ""
	state.PairingExpiresAt = 0
	state.PairingFailures = 0
	state.CompletionTokenHash = ""
	state.CompletionExpiresAt = 0
	state.Processed = map[string]processed{}
	return stateStore.save(state)
}

func installService(listen string) error {
	if os.Geteuid() != 0 {
		return errors.New("install-service must run as root")
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	binaryPath := "/usr/local/bin/wolnut-agent"
	data, err := os.ReadFile(executable)
	if err != nil {
		return err
	}
	if err := os.WriteFile(binaryPath, data, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(defaultStateDir, 0o700); err != nil {
		return err
	}
	unit := `[Unit]
Description=Wolnut secure shutdown agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wolnut-agent serve --listen ` + listen + `
Restart=on-failure
RestartSec=5
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/wolnut-agent
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
`
	if err := os.WriteFile("/etc/systemd/system/wolnut-agent.service", []byte(unit), 0o644); err != nil {
		return err
	}
	for _, args := range [][]string{{"daemon-reload"}, {"enable", "--now", "wolnut-agent.service"}} {
		if output, err := exec.Command("/usr/bin/systemctl", args...).CombinedOutput(); err != nil {
			return fmt.Errorf("systemctl %s failed: %s", strings.Join(args, " "), strings.TrimSpace(string(output)))
		}
	}
	return nil
}

func runServe(stateDir, listen string) error {
	stateStore := &store{dir: stateDir}
	if _, err := stateStore.initialize(); err != nil {
		return err
	}
	service := &server{
		store: stateStore, poweroff: systemdPoweroff{}, shutdownDelay: 2 * time.Second,
	}
	httpServer := &http.Server{
		Addr:              listen,
		Handler:           service.routes(),
		TLSConfig:         service.tlsConfig(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    16 * 1024,
	}
	log.Printf("wolnut-agent %s listening on %s", version, listen)
	return httpServer.ListenAndServeTLS("", "")
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: wolnut-agent <serve|pairing-code|reset-pairing|install-service|version>")
		os.Exit(2)
	}
	command := os.Args[1]
	flags := flag.NewFlagSet(command, flag.ExitOnError)
	stateDir := flags.String("state-dir", defaultStateDir, "agent state directory")
	listen := flags.String("listen", defaultListen, "listen address")
	confirm := flags.Bool("confirm", false, "confirm destructive action")
	_ = flags.Parse(os.Args[2:])
	stateStore := &store{dir: *stateDir}
	var err error
	switch command {
	case "serve":
		err = runServe(*stateDir, *listen)
	case "pairing-code":
		err = pairingCode(stateStore)
	case "reset-pairing":
		err = resetPairing(stateStore, *confirm)
	case "install-service":
		err = installService(*listen)
	case "version", "--version":
		fmt.Printf("wolnut-agent %s protocol v%d\n", version, protocolVersion)
	default:
		err = fmt.Errorf("unknown command %q", command)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
