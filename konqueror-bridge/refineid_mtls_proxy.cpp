// Copyright 2026 Petri Koistinen
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// A per-user loopback HTTP CONNECT proxy for QtWebEngine, which cannot use a
// PKCS#11 key for TLS client authentication. CONNECT to a host of
// kCardHosts on port 443 is terminated here: the browser side uses this
// bridge's own key, the server side is verified against the system trust
// store and the server's name, and the card key answers a client
// certificate request. Every other CONNECT is relayed untouched.

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

#include <string>

#include <openssl/bio.h>
#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <openssl/store.h>
#include <openssl/ui.h>
#include <openssl/x509.h>
#include <openssl/x509v3.h>

#ifdef REFINEID_DEBUG
#define DEBUG_LOG(message) fprintf(stderr, "refineid-mtls-proxy: %s\n", message)
#define DEBUG_ERRORS() ERR_print_errors_fp(stderr)
#else
#define DEBUG_LOG(message) ((void)0)
#define DEBUG_ERRORS() ERR_clear_error()
#endif

namespace {

const int kListenPort = 18443;
const int kHeaderTimeoutSeconds = 10;
const int kIdleTimeoutMs = 300000;
const size_t kMaxRequest = 8192;
const size_t kSecretBytes = 32;
const long kBridgeCertDays = 3650;

const char *const kCardHosts[] = {
    "card.refineid.fi", "suomi.fi", "fineid.fi", "dvv.fi", "vrk.fi",
#ifdef REFINEID_TEST_HOST
    REFINEID_TEST_HOST,
#endif
};

// A card in a reader, then the card of a paired phone.
const char *const kCardTokens[] = {"Basic%20PIN%201", "Phone%20Basic%20PIN%201"};
const char kAskPin[] = "/usr/local/bin/refineid-ask-pin";

SSL_CTX *g_browser_side = nullptr;
SSL_CTX *g_server_side = nullptr;
std::string g_expected_credentials;

// The card's certificate and key, kept while they work.
pthread_mutex_t g_card_lock = PTHREAD_MUTEX_INITIALIZER;
X509 *g_card_cert = nullptr;
EVP_PKEY *g_card_key = nullptr;

// True for a name in kCardHosts or a subdomain of one. `host` is lower case.
bool is_card_host(const std::string &host) {
    for (const char *listed : kCardHosts) {
        const size_t length = strlen(listed);
        if (host == listed) {
            return true;
        }
        if (host.size() > length && host[host.size() - length - 1] == '.' &&
            host.compare(host.size() - length, length, listed) == 0) {
            return true;
        }
    }
    return false;
}

int pin_reader(UI *ui, UI_STRING *uis) {
    const UI_string_types type = UI_get_string_type(uis);
    if (type != UIT_PROMPT && type != UIT_VERIFY) {
        return 1;
    }
    int pipe_ends[2];
    if (pipe(pipe_ends) != 0) {
        return 0;
    }
    const pid_t child = fork();
    if (child < 0) {
        close(pipe_ends[0]);
        close(pipe_ends[1]);
        return 0;
    }
    if (child == 0) {
        dup2(pipe_ends[1], STDOUT_FILENO);
        close(pipe_ends[0]);
        close(pipe_ends[1]);
        execl(kAskPin, kAskPin, static_cast<char *>(nullptr));
        _exit(127);
    }
    close(pipe_ends[1]);
    char pin[64];
    size_t length = 0;
    while (length < sizeof(pin) - 1) {
        const ssize_t got = read(pipe_ends[0], pin + length, sizeof(pin) - 1 - length);
        if (got <= 0) {
            break;
        }
        length += static_cast<size_t>(got);
    }
    close(pipe_ends[0]);
    pin[length] = '\0';
    pin[strcspn(pin, "\r\n")] = '\0';
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
    // A cancelled dialog must not reach the card as an empty PIN.
    int answered = 0;
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0 && pin[0] != '\0') {
        answered = UI_set_result(ui, uis, pin) == 0 ? 1 : 0;
    }
    OPENSSL_cleanse(pin, sizeof(pin));
    return answered;
}

void forget_card_locked() {
    X509_free(g_card_cert);
    EVP_PKEY_free(g_card_key);
    g_card_cert = nullptr;
    g_card_key = nullptr;
}

void forget_card() {
    pthread_mutex_lock(&g_card_lock);
    forget_card_locked();
    pthread_mutex_unlock(&g_card_lock);
}

// Calls `take` on each object of `uri` until it answers true.
template <typename Take>
void read_store(const std::string &uri, UI_METHOD *method, Take take) {
    OSSL_STORE_CTX *store = OSSL_STORE_open(uri.c_str(), method, nullptr, nullptr, nullptr);
    if (store == nullptr) {
        return;
    }
    bool done = false;
    while (!done && !OSSL_STORE_eof(store) && !OSSL_STORE_error(store)) {
        OSSL_STORE_INFO *info = OSSL_STORE_load(store);
        if (info != nullptr) {
            done = take(info);
            OSSL_STORE_INFO_free(info);
        }
    }
    OSSL_STORE_close(store);
}

bool load_card_locked() {
    if (g_card_cert != nullptr && g_card_key != nullptr) {
        return true;
    }
    forget_card_locked();
    UI_METHOD *method = UI_create_method("refineid-pin");
    if (method == nullptr) {
        return false;
    }
    UI_method_set_reader(method, pin_reader);
    for (const char *token : kCardTokens) {
        const std::string base = std::string("pkcs11:token=") + token;
        read_store(base + ";type=cert", method, [](OSSL_STORE_INFO *info) {
            if (OSSL_STORE_INFO_get_type(info) != OSSL_STORE_INFO_CERT) {
                return false;
            }
            X509 *cert = OSSL_STORE_INFO_get1_CERT(info);
            if (X509_check_ca(cert) != 0) {
                X509_free(cert);
                return false;
            }
            g_card_cert = cert;
            return true;
        });
        if (g_card_cert == nullptr) {
            continue;
        }
        read_store(base + ";type=private", method, [](OSSL_STORE_INFO *info) {
            if (OSSL_STORE_INFO_get_type(info) != OSSL_STORE_INFO_PKEY) {
                return false;
            }
            g_card_key = OSSL_STORE_INFO_get1_PKEY(info);
            return g_card_key != nullptr;
        });
        break;
    }
    UI_destroy_method(method);
    if (g_card_key == nullptr) {
        forget_card_locked();
        DEBUG_ERRORS();
        return false;
    }
    return true;
}

// Called only when a server asks for a client certificate, so a
// page that does not ask never prompts for PIN 1.
int client_cert_callback(SSL *, X509 **cert, EVP_PKEY **key) {
    pthread_mutex_lock(&g_card_lock);
    int result = 0;
    if (load_card_locked()) {
        X509_up_ref(g_card_cert);
        EVP_PKEY_up_ref(g_card_key);
        *cert = g_card_cert;
        *key = g_card_key;
        result = 1;
    }
    pthread_mutex_unlock(&g_card_lock);
    return result;
}

bool write_all(int fd, const char *data, size_t length) {
    while (length > 0) {
        const ssize_t sent = write(fd, data, length);
        if (sent < 0 && errno == EINTR) {
            continue;
        }
        if (sent < 0 && errno == EAGAIN) {
            struct pollfd wait = {fd, POLLOUT, 0};
            if (poll(&wait, 1, kIdleTimeoutMs) <= 0) {
                return false;
            }
            continue;
        }
        if (sent <= 0) {
            return false;
        }
        data += sent;
        length -= static_cast<size_t>(sent);
    }
    return true;
}

bool write_text(int fd, const char *text) { return write_all(fd, text, strlen(text)); }

int connect_tcp(const std::string &host, int port) {
    const std::string service = std::to_string(port);
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    struct addrinfo *found = nullptr;
    if (getaddrinfo(host.c_str(), service.c_str(), &hints, &found) != 0) {
        return -1;
    }
    int sock = -1;
    for (struct addrinfo *entry = found; entry != nullptr; entry = entry->ai_next) {
        sock = socket(entry->ai_family, entry->ai_socktype | SOCK_CLOEXEC, entry->ai_protocol);
        if (sock < 0) {
            continue;
        }
        if (connect(sock, entry->ai_addr, entry->ai_addrlen) == 0) {
            break;
        }
        close(sock);
        sock = -1;
    }
    freeaddrinfo(found);
    return sock;
}

void relay_raw(int browser, int server) {
    struct pollfd fds[2] = {{browser, POLLIN, 0}, {server, POLLIN, 0}};
    char buffer[16384];
    while (poll(fds, 2, kIdleTimeoutMs) > 0) {
        for (int side = 0; side < 2; side++) {
            if ((fds[side].revents & (POLLIN | POLLHUP | POLLERR)) == 0) {
                continue;
            }
            const ssize_t got = read(fds[side].fd, buffer, sizeof(buffer));
            if (got <= 0 || !write_all(fds[1 - side].fd, buffer, static_cast<size_t>(got))) {
                return;
            }
        }
    }
}

bool set_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);
    return flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

// Waits for what the failed call on `ssl` asks for.
bool wait_for(SSL *ssl, int result) {
    const int error = SSL_get_error(ssl, result);
    short events = 0;
    if (error == SSL_ERROR_WANT_READ) {
        events = POLLIN;
    } else if (error == SSL_ERROR_WANT_WRITE) {
        events = POLLOUT;
    } else {
        return false;
    }
    struct pollfd wait = {SSL_get_fd(ssl), events, 0};
    return poll(&wait, 1, kIdleTimeoutMs) > 0;
}

// Moves everything readable now from `from` to `to`. False ends the relay.
bool pump(SSL *from, SSL *to) {
    char buffer[16384];
    while (true) {
        const int got = SSL_read(from, buffer, sizeof(buffer));
        if (got <= 0) {
            const int error = SSL_get_error(from, got);
            return error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE;
        }
        int offset = 0;
        while (offset < got) {
            const int sent = SSL_write(to, buffer + offset, got - offset);
            if (sent > 0) {
                offset += sent;
            } else if (!wait_for(to, sent)) {
                return false;
            }
        }
    }
}

void relay_tls(SSL *browser, SSL *server) {
    if (!set_nonblocking(SSL_get_fd(browser)) || !set_nonblocking(SSL_get_fd(server))) {
        return;
    }
    SSL_set_mode(browser, SSL_MODE_ENABLE_PARTIAL_WRITE);
    SSL_set_mode(server, SSL_MODE_ENABLE_PARTIAL_WRITE);
    struct pollfd fds[2] = {{SSL_get_fd(browser), POLLIN, 0}, {SSL_get_fd(server), POLLIN, 0}};
    do {
        if (!pump(browser, server) || !pump(server, browser)) {
            return;
        }
    } while (poll(fds, 2, kIdleTimeoutMs) > 0);
}

void terminate_tls(int browser, int server, const std::string &host) {
    SSL *browser_tls = SSL_new(g_browser_side);
    SSL *server_tls = SSL_new(g_server_side);
    if (browser_tls != nullptr && server_tls != nullptr && SSL_set_fd(browser_tls, browser) == 1 &&
        SSL_set_fd(server_tls, server) == 1 && SSL_set_tlsext_host_name(server_tls, host.c_str()) == 1 &&
        SSL_set1_host(server_tls, host.c_str()) == 1) {
        // The server is verified before the browser's request reaches it and
        // before the card key can answer anything.
        if (SSL_accept(browser_tls) == 1) {
            if (SSL_connect(server_tls) == 1) {
                DEBUG_LOG("relaying a card host");
                relay_tls(browser_tls, server_tls);
                SSL_shutdown(server_tls);
                SSL_shutdown(browser_tls);
            } else {
                DEBUG_LOG("server handshake failed");
                DEBUG_ERRORS();
                // A removed card fails here; the next attempt reads it again.
                if (SSL_get_verify_result(server_tls) == X509_V_OK) {
                    forget_card();
                }
            }
        } else {
            DEBUG_LOG("browser handshake failed");
            DEBUG_ERRORS();
        }
    }
    SSL_free(server_tls);
    SSL_free(browser_tls);
}

// Value of `name` in the request's header lines, without surrounding space.
std::string header_value(const std::string &request, const char *name) {
    const size_t name_length = strlen(name);
    size_t line = request.find("\r\n");
    while (line != std::string::npos) {
        const size_t start = line + 2;
        line = request.find("\r\n", start);
        if (line == std::string::npos) {
            break;
        }
        if (line - start > name_length && request[start + name_length] == ':' &&
            strncasecmp(request.c_str() + start, name, name_length) == 0) {
            size_t value = start + name_length + 1;
            while (value < line && request[value] == ' ') {
                value++;
            }
            return request.substr(value, line - value);
        }
    }
    return std::string();
}

bool credentials_match(const std::string &request) {
    const std::string offered = header_value(request, "Proxy-Authorization");
    return offered.size() == g_expected_credentials.size() &&
           CRYPTO_memcmp(offered.data(), g_expected_credentials.data(), offered.size()) == 0;
}

// Splits "host:port" or "[v6]:port"; false for anything else.
bool parse_target(const std::string &target, std::string *host, int *port) {
    const size_t colon = target.rfind(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 == target.size() || target.size() - colon > 6) {
        return false;
    }
    int value = 0;
    for (size_t index = colon + 1; index < target.size(); index++) {
        if (target[index] < '0' || target[index] > '9') {
            return false;
        }
        value = value * 10 + (target[index] - '0');
    }
    if (value < 1 || value > 65535) {
        return false;
    }
    std::string name = target.substr(0, colon);
    if (name.size() > 2 && name.front() == '[' && name.back() == ']') {
        name = name.substr(1, name.size() - 2);
    }
    for (char &byte : name) {
        if (byte >= 'A' && byte <= 'Z') {
            byte = static_cast<char>(byte - 'A' + 'a');
        }
    }
    *host = name;
    *port = value;
    return true;
}

void handle_client(int browser) {
    struct timeval timeout = {kHeaderTimeoutSeconds, 0};
    setsockopt(browser, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    std::string request;
    char byte;
    while (request.size() < kMaxRequest &&
           (request.size() < 4 || request.compare(request.size() - 4, 4, "\r\n\r\n") != 0)) {
        if (read(browser, &byte, 1) != 1) {
            return;
        }
        request.push_back(byte);
    }
    if (request.size() >= kMaxRequest) {
        return;
    }
    if (!credentials_match(request)) {
        write_text(browser,
                   "HTTP/1.1 407 Proxy Authentication Required\r\n"
                   "Proxy-Authenticate: Basic realm=\"refineid\"\r\n"
                   "Content-Length: 0\r\nConnection: close\r\n\r\n");
        return;
    }
    const size_t space = request.find(' ');
    const size_t second = space == std::string::npos ? space : request.find(' ', space + 1);
    std::string host;
    int port = 0;
    if (second == std::string::npos || request.compare(0, space, "CONNECT") != 0 ||
        !parse_target(request.substr(space + 1, second - space - 1), &host, &port)) {
        write_text(browser, "HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        return;
    }
    const int server = connect_tcp(host, port);
    if (server < 0) {
        write_text(browser, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        return;
    }
    timeout.tv_sec = 0;
    setsockopt(browser, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    if (write_text(browser, "HTTP/1.1 200 Connection Established\r\n\r\n")) {
        if (port == 443 && is_card_host(host)) {
            terminate_tls(browser, server, host);
        } else {
            relay_raw(browser, server);
        }
    }
    close(server);
}

void *client_thread(void *argument) {
    const int browser = static_cast<int>(reinterpret_cast<intptr_t>(argument));
    handle_client(browser);
    close(browser);
    return nullptr;
}

std::string bridge_directory() {
    const char *data = getenv("XDG_DATA_HOME");
    if (data != nullptr && data[0] == '/') {
        return std::string(data) + "/refineid/bridge";
    }
    const char *home = getenv("HOME");
    return home == nullptr ? std::string() : std::string(home) + "/.local/share/refineid/bridge";
}

bool make_directories(const std::string &path) {
    for (size_t slash = path.find('/', 1); slash != std::string::npos; slash = path.find('/', slash + 1)) {
        if (mkdir(path.substr(0, slash).c_str(), 0700) != 0 && errno != EEXIST) {
            return false;
        }
    }
    return mkdir(path.c_str(), 0700) == 0 || errno == EEXIST;
}

bool read_file(const std::string &path, std::string *contents) {
    FILE *file = fopen(path.c_str(), "r");
    if (file == nullptr) {
        return false;
    }
    char buffer[512];
    contents->clear();
    size_t got;
    while ((got = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        contents->append(buffer, got);
    }
    fclose(file);
    return true;
}

bool write_private_file(const std::string &path, const std::string &contents) {
    const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        return false;
    }
    const bool written = write_all(fd, contents.data(), contents.size());
    return close(fd) == 0 && written;
}

std::string base64(const unsigned char *data, size_t length) {
    std::string encoded(4 * ((length + 2) / 3) + 1, '\0');
    const int written = EVP_EncodeBlock(reinterpret_cast<unsigned char *>(&encoded[0]), data, static_cast<int>(length));
    encoded.resize(static_cast<size_t>(written));
    return encoded;
}

// Creates the bridge's self-signed certificate and its pin: the base64
// SHA-256 of the public key, which is what the browser side trusts.
bool create_identity(const std::string &directory) {
    EVP_PKEY *key = EVP_EC_gen("P-256");
    X509 *cert = X509_new();
    bool done = false;
    if (key != nullptr && cert != nullptr) {
        unsigned char serial[16];
        BIGNUM *number = nullptr;
        if (RAND_bytes(serial, sizeof(serial)) == 1 && (number = BN_bin2bn(serial, sizeof(serial), nullptr)) != nullptr) {
            BN_clear_bit(number, 127);
            BN_to_ASN1_INTEGER(number, X509_get_serialNumber(cert));
            BN_free(number);
            X509_set_version(cert, X509_VERSION_3);
            X509_gmtime_adj(X509_getm_notBefore(cert), 0);
            X509_time_adj_ex(X509_getm_notAfter(cert), kBridgeCertDays, 0, nullptr);
            X509_set_pubkey(cert, key);
            X509_NAME *name = X509_get_subject_name(cert);
            X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC, reinterpret_cast<const unsigned char *>("RefineID bridge"), -1, -1, 0);
            X509_set_issuer_name(cert, name);
            done = X509_sign(cert, key, EVP_sha256()) > 0;
        }
    }
    if (done) {
        const std::string key_path = directory + "/key.pem";
        const std::string cert_path = directory + "/cert.pem";
        BIO *key_out = BIO_new(BIO_s_mem());
        BIO *cert_out = BIO_new(BIO_s_mem());
        done = key_out != nullptr && cert_out != nullptr &&
               PEM_write_bio_PrivateKey(key_out, key, nullptr, nullptr, 0, nullptr, nullptr) == 1 &&
               PEM_write_bio_X509(cert_out, cert) == 1;
        if (done) {
            char *bytes = nullptr;
            long length = BIO_get_mem_data(key_out, &bytes);
            done = write_private_file(key_path, std::string(bytes, static_cast<size_t>(length)));
            length = BIO_get_mem_data(cert_out, &bytes);
            done = done && write_private_file(cert_path, std::string(bytes, static_cast<size_t>(length)));
        }
        BIO_free(key_out);
        BIO_free(cert_out);
    }
    if (done) {
        unsigned char *spki = nullptr;
        const int length = i2d_PUBKEY(key, &spki);
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int digest_length = 0;
        done = length > 0 && EVP_Digest(spki, static_cast<size_t>(length), digest, &digest_length, EVP_sha256(), nullptr) == 1 &&
               write_private_file(directory + "/spki-sha256.base64", base64(digest, digest_length) + "\n");
        OPENSSL_free(spki);
    }
    X509_free(cert);
    EVP_PKEY_free(key);
    return done;
}

// Returns the expected Proxy-Authorization value, creating the secret once.
bool load_credentials(const std::string &directory) {
    const std::string path = directory + "/proxy-secret";
    std::string secret;
    if (!read_file(path, &secret)) {
        unsigned char random[kSecretBytes];
        if (RAND_bytes(random, sizeof(random)) != 1) {
            return false;
        }
        secret = base64(random, sizeof(random));
        OPENSSL_cleanse(random, sizeof(random));
        if (!write_private_file(path, secret + "\n")) {
            return false;
        }
    }
    while (!secret.empty() && (secret.back() == '\n' || secret.back() == '\r')) {
        secret.pop_back();
    }
    if (secret.empty()) {
        return false;
    }
    const std::string pair = "refineid:" + secret;
    g_expected_credentials = "Basic " + base64(reinterpret_cast<const unsigned char *>(pair.data()), pair.size());
    return true;
}

bool fail(const char *message) {
    fprintf(stderr, "refineid-mtls-proxy: %s\n", message);
    return false;
}

bool initialise_tls(const std::string &directory) {
    const std::string cert_path = directory + "/cert.pem";
    const std::string key_path = directory + "/key.pem";
    if (access(key_path.c_str(), R_OK) != 0 && !create_identity(directory)) {
        return fail("cannot create the bridge identity");
    }
    g_browser_side = SSL_CTX_new(TLS_server_method());
    g_server_side = SSL_CTX_new(TLS_client_method());
    if (g_browser_side == nullptr || g_server_side == nullptr ||
        SSL_CTX_set_min_proto_version(g_browser_side, TLS1_2_VERSION) != 1 ||
        SSL_CTX_set_min_proto_version(g_server_side, TLS1_2_VERSION) != 1 ||
        SSL_CTX_use_certificate_file(g_browser_side, cert_path.c_str(), SSL_FILETYPE_PEM) != 1 ||
        SSL_CTX_use_PrivateKey_file(g_browser_side, key_path.c_str(), SSL_FILETYPE_PEM) != 1 ||
        SSL_CTX_set_default_verify_paths(g_server_side) != 1) {
        return fail("cannot set up TLS");
    }
    SSL_CTX_set_verify(g_server_side, SSL_VERIFY_PEER, nullptr);
    SSL_CTX_set_client_cert_cb(g_server_side, client_cert_callback);
    return true;
}

}  // namespace

int main() {
    signal(SIGPIPE, SIG_IGN);
    umask(077);
    setenv("OPENSSL_CONF", "/usr/local/etc/refineid/openssl_pkcs11.cnf", 0);
    if (OPENSSL_init_crypto(OPENSSL_INIT_LOAD_CONFIG, nullptr) == 0) {
        fail("cannot load the OpenSSL configuration");
        return 1;
    }
    const std::string directory = bridge_directory();
    if (directory.empty() || !make_directories(directory)) {
        fail("cannot create the bridge directory");
        return 1;
    }
    if (!initialise_tls(directory) || !load_credentials(directory)) {
        fail("cannot read the bridge identity");
        return 1;
    }

    const int listener = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    const int reuse = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(kListenPort);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(listener, reinterpret_cast<struct sockaddr *>(&address), sizeof(address)) != 0 ||
        listen(listener, 64) != 0) {
        fail("cannot listen on the loopback port");
        return 1;
    }
    while (true) {
        const int browser = accept4(listener, nullptr, nullptr, SOCK_CLOEXEC);
        if (browser < 0) {
            continue;
        }
        pthread_t thread;
        if (pthread_create(&thread, nullptr, client_thread, reinterpret_cast<void *>(static_cast<intptr_t>(browser))) == 0) {
            pthread_detach(thread);
        } else {
            close(browser);
        }
    }
}
