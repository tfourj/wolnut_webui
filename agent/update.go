package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	defaultDownloadBase = "https://github.com/tfourj/wolnut_webui/releases/latest/download"
	manifestName        = "agent-release.json"
	maxMetadataBytes    = 1024 * 1024
	maxAgentBytes       = 128 * 1024 * 1024
)

var stableVersionPattern = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)

type releaseManifest struct {
	Version         string `json:"version"`
	ProtocolVersion int    `json:"protocol_version"`
}

type updateResult struct {
	Status        string
	LatestVersion string
}

type updateExecutor interface {
	Install(currentVersion string) (updateResult, error)
	Restart() error
}

type releaseUpdater struct {
	downloadBase string
	binaryPath   string
	client       *http.Client
	run          commandExecutor
	architecture string
}

func validateDownloadBase(value string) (string, error) {
	trimmed := strings.TrimRight(strings.TrimSpace(value), "/")
	parsed, err := url.Parse(trimmed)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil ||
		parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", errors.New("agent download base must be an HTTPS URL without credentials, query, or fragment")
	}
	return trimmed, nil
}

func updateHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 30 * time.Second,
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return errors.New("too many update redirects")
			}
			if request.URL.Scheme != "https" {
				return errors.New("update redirect must use HTTPS")
			}
			return nil
		},
	}
}

func (u *releaseUpdater) fetch(name string, maximum int64) ([]byte, error) {
	base, err := validateDownloadBase(u.downloadBase)
	if err != nil {
		return nil, err
	}
	client := u.client
	if client == nil {
		client = updateHTTPClient()
	}
	request, err := http.NewRequest(http.MethodGet, base+"/"+name, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", "wolnut-agent/"+version)
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("download %s: %w", name, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download %s returned status %d", name, response.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, maximum+1))
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", name, err)
	}
	if int64(len(data)) > maximum {
		return nil, fmt.Errorf("download %s exceeds size limit", name)
	}
	return data, nil
}

func checksumFor(checksumFile []byte, expectedName string) (string, error) {
	fields := strings.Fields(strings.TrimSpace(string(checksumFile)))
	if len(fields) != 2 || strings.TrimPrefix(fields[1], "*") != expectedName {
		return "", errors.New("checksum file does not match expected asset")
	}
	checksum := strings.ToLower(fields[0])
	if len(checksum) != 64 {
		return "", errors.New("checksum must contain 64 hexadecimal characters")
	}
	if _, err := hex.DecodeString(checksum); err != nil {
		return "", errors.New("checksum is not valid hexadecimal")
	}
	return checksum, nil
}

func verifyChecksum(data []byte, checksum string) error {
	digest := sha256.Sum256(data)
	if !strings.EqualFold(hex.EncodeToString(digest[:]), checksum) {
		return errors.New("download checksum does not match")
	}
	return nil
}

func parseStableVersion(value string) ([3]int, error) {
	var parts [3]int
	if !stableVersionPattern.MatchString(value) {
		return parts, errors.New("release version must use stable x.y.z format")
	}
	for index, component := range strings.Split(value, ".") {
		parsed, err := strconv.Atoi(component)
		if err != nil {
			return parts, errors.New("release version is invalid")
		}
		parts[index] = parsed
	}
	return parts, nil
}

func newerVersion(current, available string) (bool, error) {
	currentParts, err := parseStableVersion(current)
	if err != nil {
		return false, fmt.Errorf("current agent version is not updateable: %w", err)
	}
	availableParts, err := parseStableVersion(available)
	if err != nil {
		return false, err
	}
	for index := range currentParts {
		if availableParts[index] != currentParts[index] {
			return availableParts[index] > currentParts[index], nil
		}
	}
	return false, nil
}

func (u *releaseUpdater) manifest() (releaseManifest, error) {
	var manifest releaseManifest
	data, err := u.fetch(manifestName, maxMetadataBytes)
	if err != nil {
		return manifest, err
	}
	checksumData, err := u.fetch(manifestName+".sha256", maxMetadataBytes)
	if err != nil {
		return manifest, err
	}
	checksum, err := checksumFor(checksumData, manifestName)
	if err != nil {
		return manifest, err
	}
	if err := verifyChecksum(data, checksum); err != nil {
		return manifest, fmt.Errorf("release manifest: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&manifest); err != nil {
		return manifest, errors.New("release manifest is invalid")
	}
	if _, err := parseStableVersion(manifest.Version); err != nil {
		return manifest, err
	}
	if manifest.ProtocolVersion != protocolVersion {
		return manifest, errors.New("release protocol version is incompatible")
	}
	return manifest, nil
}

func (u *releaseUpdater) Install(currentVersion string) (updateResult, error) {
	manifest, err := u.manifest()
	if err != nil {
		return updateResult{}, err
	}
	newer, err := newerVersion(currentVersion, manifest.Version)
	if err != nil {
		return updateResult{}, err
	}
	result := updateResult{Status: "up_to_date", LatestVersion: manifest.Version}
	if !newer {
		return result, nil
	}

	architecture := u.architecture
	if architecture == "" {
		architecture = runtime.GOARCH
	}
	if architecture != "amd64" && architecture != "arm64" {
		return updateResult{}, fmt.Errorf("unsupported update architecture: %s", architecture)
	}
	assetName := "wolnut-agent-linux-" + architecture
	binary, err := u.fetch(assetName, maxAgentBytes)
	if err != nil {
		return updateResult{}, err
	}
	checksumData, err := u.fetch(assetName+".sha256", maxMetadataBytes)
	if err != nil {
		return updateResult{}, err
	}
	checksum, err := checksumFor(checksumData, assetName)
	if err != nil {
		return updateResult{}, err
	}
	if err := verifyChecksum(binary, checksum); err != nil {
		return updateResult{}, fmt.Errorf("agent binary: %w", err)
	}

	binaryPath := u.binaryPath
	if binaryPath == "" {
		binaryPath = filepath.Join(defaultStateDir, "wolnut-agent")
	}
	if err := os.MkdirAll(filepath.Dir(binaryPath), 0o700); err != nil {
		return updateResult{}, err
	}
	temporary, err := os.CreateTemp(filepath.Dir(binaryPath), ".wolnut-agent-update-*")
	if err != nil {
		return updateResult{}, err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o755); err != nil {
		temporary.Close()
		return updateResult{}, err
	}
	if _, err := temporary.Write(binary); err != nil {
		temporary.Close()
		return updateResult{}, err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return updateResult{}, err
	}
	if err := temporary.Close(); err != nil {
		return updateResult{}, err
	}
	run := u.run
	if run == nil {
		run = func(name string, args ...string) error {
			output, err := exec.Command(name, args...).CombinedOutput()
			if err != nil {
				return fmt.Errorf("%w: %s", err, strings.TrimSpace(string(output)))
			}
			if strings.TrimSpace(string(output)) != fmt.Sprintf("wolnut-agent %s protocol v%d", manifest.Version, protocolVersion) {
				return errors.New("downloaded agent reports an unexpected version")
			}
			return nil
		}
	}
	if err := run(temporaryPath, "version"); err != nil {
		return updateResult{}, fmt.Errorf("verify downloaded agent: %w", err)
	}
	if err := os.Rename(temporaryPath, binaryPath); err != nil {
		return updateResult{}, fmt.Errorf("install agent update: %w", err)
	}
	return updateResult{Status: "restart_pending", LatestVersion: manifest.Version}, nil
}

func (u *releaseUpdater) Restart() error {
	run := u.run
	if run == nil {
		run = func(name string, args ...string) error {
			return exec.Command(name, args...).Run()
		}
	}
	return run("/usr/bin/systemctl", "restart", "wolnut-agent.service")
}
