package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
