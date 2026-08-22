package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type fakePoweroff struct{ calls atomic.Int32 }

func (f *fakePoweroff) Poweroff() error {
	f.calls.Add(1)
	return nil
}

func TestStoreUsesPrivatePermissions(t *testing.T) {
	dir := t.TempDir()
	s := &store{dir: filepath.Join(dir, "agent")}
	if _, err := s.initialize(); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(s.path())
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("state permissions are %o", info.Mode().Perm())
	}
}

func TestPairingCodeExpiresAndLocksAfterFailures(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	state.PairingCodeHash = "wrong"
	state.PairingExpiresAt = time.Now().Add(time.Minute).Unix()
	if err := s.save(state); err != nil {
		t.Fatal(err)
	}
	service := &server{store: s, poweroff: &fakePoweroff{}}
	for i := 0; i < 5; i++ {
		state, _ = s.load()
		if err := service.validatePairingCode(state, "bad"); err == nil {
			t.Fatal("invalid pairing code was accepted")
		}
	}
	state, _ = s.load()
	if state.PairingFailures != 5 {
		t.Fatalf("expected 5 failures, got %d", state.PairingFailures)
	}
	if err := service.validatePairingCode(state, "bad"); err == nil {
		t.Fatal("locked pairing code was accepted")
	}
}

func TestExpiredPairingCodeIsRejected(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	_, codeHash, err := newSecret()
	if err != nil {
		t.Fatal(err)
	}
	state.PairingCodeHash = codeHash
	state.PairingExpiresAt = time.Now().Add(-time.Second).Unix()
	if err := s.save(state); err != nil {
		t.Fatal(err)
	}

	if err := (&server{store: s}).validatePairingCode(state, "anything"); err == nil {
		t.Fatal("expired pairing code was accepted")
	}
}

func TestShutdownRequestValidation(t *testing.T) {
	now := time.Now()
	valid := shutdownRequest{
		CommandID:   "outage:server",
		Source:      "automatic",
		RequestedAt: now.Unix(),
		ExpiresAt:   now.Add(time.Minute).Unix(),
	}
	if err := validateShutdownRequest(valid, now); err != nil {
		t.Fatal(err)
	}
	invalid := valid
	invalid.ExpiresAt = now.Add(-time.Second).Unix()
	if err := validateShutdownRequest(invalid, now); err == nil {
		t.Fatal("expired request was accepted")
	}
	invalid = valid
	invalid.Source = "shell"
	if !errors.Is(validateShutdownRequest(invalid, now), errors.New("invalid shutdown source")) && validateShutdownRequest(invalid, now) == nil {
		t.Fatal("invalid source was accepted")
	}
}

func TestProcessedCommandsAreBounded(t *testing.T) {
	values := map[string]processed{}
	for i := 0; i < 120; i++ {
		values[string(rune(i))] = processed{AcceptedAt: int64(i)}
	}
	pruneProcessed(values, 100)
	if len(values) != 100 {
		t.Fatalf("expected 100 commands, got %d", len(values))
	}
}

func TestShutdownHandlerIsIdempotent(t *testing.T) {
	s := &store{dir: t.TempDir()}
	if _, err := s.initialize(); err != nil {
		t.Fatal(err)
	}
	executor := &fakePoweroff{}
	service := &server{store: s, poweroff: executor, shutdownDelay: 0}
	now := time.Now()
	payload, _ := json.Marshal(shutdownRequest{
		CommandID: "outage:server", Source: "automatic",
		RequestedAt: now.Unix(), ExpiresAt: now.Add(time.Minute).Unix(),
	})

	for attempt := 0; attempt < 2; attempt++ {
		request := httptest.NewRequest(http.MethodPost, "/v1/shutdown", bytes.NewReader(payload))
		response := httptest.NewRecorder()
		service.handleShutdown(response, request)
		if response.Code != http.StatusAccepted && response.Code != http.StatusOK {
			t.Fatalf("unexpected response %d: %s", response.Code, response.Body.String())
		}
	}
	time.Sleep(10 * time.Millisecond)
	if executor.calls.Load() != 1 {
		t.Fatalf("expected one poweroff, got %d", executor.calls.Load())
	}
}

func TestConcurrentShutdownReplaysScheduleOnlyOnce(t *testing.T) {
	s := &store{dir: t.TempDir()}
	if _, err := s.initialize(); err != nil {
		t.Fatal(err)
	}
	executor := &fakePoweroff{}
	service := &server{store: s, poweroff: executor, shutdownDelay: 0}
	now := time.Now()
	payload, _ := json.Marshal(shutdownRequest{
		CommandID: "outage:concurrent", Source: "automatic",
		RequestedAt: now.Unix(), ExpiresAt: now.Add(time.Minute).Unix(),
	})

	var requests sync.WaitGroup
	for attempt := 0; attempt < 20; attempt++ {
		requests.Add(1)
		go func() {
			defer requests.Done()
			request := httptest.NewRequest(http.MethodPost, "/v1/shutdown", bytes.NewReader(payload))
			response := httptest.NewRecorder()
			service.handleShutdown(response, request)
			if response.Code != http.StatusAccepted && response.Code != http.StatusOK {
				t.Errorf("unexpected response %d: %s", response.Code, response.Body.String())
			}
		}()
	}
	requests.Wait()
	time.Sleep(10 * time.Millisecond)
	if executor.calls.Load() != 1 {
		t.Fatalf("expected one poweroff, got %d", executor.calls.Load())
	}
}

func TestSystemdPoweroffUsesStaticInvocation(t *testing.T) {
	var executable string
	var arguments []string
	executor := systemdPoweroff{run: func(name string, args ...string) error {
		executable = name
		arguments = append([]string(nil), args...)
		return nil
	}}

	if err := executor.Poweroff(); err != nil {
		t.Fatal(err)
	}
	if executable != "/usr/bin/systemctl" || len(arguments) != 1 || arguments[0] != "poweroff" {
		t.Fatalf("unexpected shutdown invocation: %q %q", executable, arguments)
	}
}

func TestUnpairClearsEnrollmentAndLedger(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	state.ControllerCAPEM = "ca"
	state.ControllerCertPEM = "controller"
	state.ServerCertPEM = "server"
	state.ServerKeyPEM = "key"
	state.Processed["command"] = processed{AcceptedAt: time.Now().Unix()}
	if err := s.save(state); err != nil {
		t.Fatal(err)
	}
	service := &server{store: s, poweroff: &fakePoweroff{}}
	request := httptest.NewRequest(http.MethodPost, "/v1/unpair", bytes.NewReader([]byte("{}")))
	response := httptest.NewRecorder()

	service.handleUnpair(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected response %d: %s", response.Code, response.Body.String())
	}
	state, _ = s.load()
	if state.ControllerCAPEM != "" || state.ServerCertPEM != "" || len(state.Processed) != 0 {
		t.Fatal("unpair did not clear enrollment state")
	}
}

func TestListenAddressValidation(t *testing.T) {
	for _, value := range []string{"0.0.0.0:8184", "[::]:8184", "127.0.0.1:1"} {
		if err := validateListenAddress(value); err != nil {
			t.Fatalf("valid address %q rejected: %v", value, err)
		}
	}
	for _, value := range []string{"0.0.0.0:0", "0.0.0.0:65536", "bad", "0.0.0.0:8184\nInjected=true"} {
		if err := validateListenAddress(value); err == nil {
			t.Fatalf("invalid address %q accepted", value)
		}
	}
}

func TestOutboundEnrollmentOverHTTPS(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	var firstCSR string
	var attempts int
	controller := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("unexpected method %s", r.Method)
		}
		var request outboundEnrollmentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			return
		}
		if request.Token != "one-time-secret" || request.AgentID != state.AgentID {
			t.Errorf("unexpected enrollment identity or token")
		}
		attempts++
		if attempts == 1 {
			firstCSR = request.CSR
			writeError(w, http.StatusServiceUnavailable, "temporary failure")
			return
		}
		if request.CSR != firstCSR {
			t.Error("agent did not reuse its pending CSR after a lost response")
		}
		caPEM, controllerCertPEM, serverCertPEM := testEnrollmentCertificates(
			t, request.CSR, request.AgentID,
		)
		writeJSON(w, http.StatusOK, outboundEnrollmentResponse{
			Status: "paired", ProtocolVersion: protocolVersion, AgentID: request.AgentID,
			ControllerCAPEM: caPEM, ControllerCertPEM: controllerCertPEM,
			ServerCertPEM: serverCertPEM,
		})
	}))
	defer controller.Close()

	if err := enrollAgent(s, controller.URL, "one-time-secret", controller.Client()); err == nil {
		t.Fatal("temporary controller failure was not returned")
	}
	if err := enrollAgent(s, controller.URL, "one-time-secret", controller.Client()); err != nil {
		t.Fatal(err)
	}
	state, err = s.load()
	if err != nil {
		t.Fatal(err)
	}
	if state.ServerCertPEM == "" || state.ControllerCAPEM == "" || state.PendingServerKeyPEM != "" {
		t.Fatal("enrollment credentials were not persisted correctly")
	}
}

func TestOutboundEnrollmentRejectsInsecureURL(t *testing.T) {
	s := &store{dir: t.TempDir()}
	if err := enrollAgent(s, "http://wolnut.example/api/agents/enroll", "token", nil); err == nil {
		t.Fatal("insecure enrollment URL was accepted")
	}
}

func TestControllerCertificateIsRequiredAfterPairing(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	state.ControllerCAPEM = "configured"
	if err := s.save(state); err != nil {
		t.Fatal(err)
	}
	service := &server{store: s, poweroff: &fakePoweroff{}}
	handler := service.requireController(service.handleStatus)
	request := httptest.NewRequest(http.MethodGet, "/v1/status", nil)
	request.TLS = &tls.ConnectionState{}
	response := httptest.NewRecorder()

	handler(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", response.Code)
	}
}

func TestProtocolRequiresMutualTLSOnLoopback(t *testing.T) {
	s := &store{dir: t.TempDir()}
	state, err := s.initialize()
	if err != nil {
		t.Fatal(err)
	}
	caPEM, serverCertPEM, serverKeyPEM, controllerCertificate := testPKI(t, state.AgentID)
	state.ControllerCAPEM = caPEM
	state.ServerCertPEM = serverCertPEM
	state.ServerKeyPEM = serverKeyPEM
	if err := s.save(state); err != nil {
		t.Fatal(err)
	}
	service := &server{store: s, poweroff: &fakePoweroff{}}
	loopback := httptest.NewUnstartedServer(service.routes())
	loopback.TLS = service.tlsConfig()
	loopback.StartTLS()
	defer loopback.Close()

	requestStatus := func(certificates []tls.Certificate) int {
		client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13, InsecureSkipVerify: true, Certificates: certificates,
		}}}
		response, err := client.Get(loopback.URL + "/v1/status")
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		return response.StatusCode
	}

	if status := requestStatus(nil); status != http.StatusUnauthorized {
		t.Fatalf("expected unauthenticated request to be rejected, got %d", status)
	}
	if status := requestStatus([]tls.Certificate{controllerCertificate}); status != http.StatusOK {
		t.Fatalf("expected authenticated request to succeed, got %d", status)
	}
}

func testPKI(t *testing.T, agentID string) (string, string, string, tls.Certificate) {
	t.Helper()
	now := time.Now()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test controller CA"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	caPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})
	caCertificate, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}

	issue := func(serial int64, commonName string, usage x509.ExtKeyUsage, identity string) (string, string) {
		key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		template := &x509.Certificate{
			SerialNumber: big.NewInt(serial), Subject: pkix.Name{CommonName: commonName},
			NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
			KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{usage},
		}
		if identity != "" {
			parsedIdentity, err := url.Parse(identity)
			if err != nil {
				t.Fatal(err)
			}
			template.URIs = []*url.URL{parsedIdentity}
		}
		certificateDER, err := x509.CreateCertificate(rand.Reader, template, caCertificate, &key.PublicKey, caKey)
		if err != nil {
			t.Fatal(err)
		}
		keyDER, err := x509.MarshalPKCS8PrivateKey(key)
		if err != nil {
			t.Fatal(err)
		}
		return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})),
			string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}))
	}

	serverCertPEM, serverKeyPEM := issue(2, "agent", x509.ExtKeyUsageServerAuth, "urn:wolnut:agent:"+agentID)
	controllerCertPEM, controllerKeyPEM := issue(3, "controller", x509.ExtKeyUsageClientAuth, "")
	controllerCertificate, err := tls.X509KeyPair([]byte(controllerCertPEM), []byte(controllerKeyPEM))
	if err != nil {
		t.Fatal(err)
	}
	return string(caPEM), serverCertPEM, serverKeyPEM, controllerCertificate
}

func testEnrollmentCertificates(t *testing.T, csrPEM, agentID string) (string, string, string) {
	t.Helper()
	now := time.Now()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(10), Subject: pkix.Name{CommonName: "enrollment CA"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	caCertificate, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}
	caPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}))

	controllerKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	controllerTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(11), Subject: pkix.Name{CommonName: "controller"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	controllerDER, err := x509.CreateCertificate(
		rand.Reader, controllerTemplate, caCertificate, &controllerKey.PublicKey, caKey,
	)
	if err != nil {
		t.Fatal(err)
	}
	controllerPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: controllerDER}))

	csrBlock, _ := pem.Decode([]byte(csrPEM))
	if csrBlock == nil {
		t.Fatal("agent returned invalid CSR PEM")
	}
	csr, err := x509.ParseCertificateRequest(csrBlock.Bytes)
	if err != nil || csr.CheckSignature() != nil {
		t.Fatal("agent returned invalid CSR")
	}
	identity, _ := url.Parse("urn:wolnut:agent:" + agentID)
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(12), Subject: csr.Subject,
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		URIs:        []*url.URL{identity},
	}
	serverDER, err := x509.CreateCertificate(
		rand.Reader, serverTemplate, caCertificate, csr.PublicKey, caKey,
	)
	if err != nil {
		t.Fatal(err)
	}
	serverPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}))
	return caPEM, controllerPEM, serverPEM
}
