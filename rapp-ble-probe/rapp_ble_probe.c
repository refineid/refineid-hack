/*
 * rapp_ble_probe.c
 *
 * FreeBSD probe of a phone's RAPP GATT server (fi.refineid.rapp.ble,
 * RAPP v26.11.2 section 5), independent of the RefineID Rust stack.
 * Connects over the fixed ATT channel of an LE link and walks the
 * section 5.2 setup: ATT MTU exchange, the RAPP service, its channel and
 * bootstrap characteristics, the channel's CCCD, a bootstrap read
 * without routing, and enabling indications. With -p it then writes the
 * "pairing" routing preamble as one SAR SINGLE fragment and reads the
 * bootstrap characteristic, as a requester does to fetch a pairing
 * offer; reading the offer starts no CPace and spends no attempt.
 *
 * While waiting for a response it answers what the phone initiates:
 * indications are confirmed, an MTU exchange is answered with the same
 * Rx MTU, and every other request is refused with an Error Response, so
 * the phone's 30 s ATT transaction timer never stalls the link.
 *
 * Prints no device address. Runs as a normal user once root has enabled
 * LE on the adapter (refineid-unix doc/install-freebsd.md, "Bluetooth").
 *
 * Build (FreeBSD):
 *   cc -O2 -Wall -Wextra -o rapp_ble_probe rapp_ble_probe.c -lbluetooth
 * Usage:
 *   ./rapp_ble_probe [-p] <random-device-address>
 */

#define L2CAP_SOCKET_CHECKED
#include <sys/types.h>
#include <sys/socket.h>
#include <bluetooth.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ATT opcodes and error codes (Bluetooth Core v5.4 Vol 3 Part F 3.4). */
#define ATT_ERROR_RSP 0x01
#define ATT_MTU_REQ 0x02
#define ATT_MTU_RSP 0x03
#define ATT_FIND_INFO_REQ 0x04
#define ATT_FIND_INFO_RSP 0x05
#define ATT_FIND_BY_TYPE_VALUE_REQ 0x06
#define ATT_READ_BY_TYPE_REQ 0x08
#define ATT_READ_BY_TYPE_RSP 0x09
#define ATT_READ_REQ 0x0a
#define ATT_READ_RSP 0x0b
#define ATT_READ_BY_GROUP_REQ 0x10
#define ATT_READ_BY_GROUP_RSP 0x11
#define ATT_WRITE_REQ 0x12
#define ATT_WRITE_RSP 0x13
#define ATT_NOTIFY 0x1b
#define ATT_INDICATE 0x1d
#define ATT_CONFIRM 0x1e
#define ATT_COMMAND_FLAG 0x40
#define ATT_ERR_INVALID_HANDLE 0x01
#define ATT_ERR_INVALID_PDU 0x04
#define ATT_ERR_NOT_SUPPORTED 0x06
#define ATT_ERR_NOT_FOUND 0x0a
#define ATT_ERROR_RSP_LEN 5
#define ATT_TRANSACTION_TIMEOUT_MS 30000

/* GATT declarations and descriptors (Vol 3 Part G 3). */
#define UUID_PRIMARY 0x2800
#define UUID_CHARACTERISTIC 0x2803
#define UUID_CCCD 0x2902
#define CCCD_INDICATIONS 0x0002
#define LAST_HANDLE 0xffff
#define PROP_READ 0x02
#define PROP_WRITE 0x08
#define PROP_INDICATE 0x20

/* RAPP v26.11.2 sections 5.1 to 5.3. */
#define REQUESTED_MTU 517
#define MINIMUM_MTU 512
#define SAR_SINGLE 0x05
#define RAPP_SERVICE 0x01
#define RAPP_CHANNEL 0x02
#define RAPP_BOOTSTRAP 0x03
#define UUID128_LEN 16
#define UUID_VARIANT_OCTET 12

/*
 * 7E39FD0x-A6B5-4D78-9E11-37E28E9545F1 in wire order (little-endian);
 * octet 12 is the last octet of the first field, 0x01, 0x02 or 0x03.
 */
static const uint8_t rapp_uuid[UUID128_LEN] = {
	0xf1, 0x45, 0x95, 0x8e, 0xe2, 0x37, 0x11, 0x9e,
	0x78, 0x4d, 0xb5, 0xa6, 0x00, 0xfd, 0x39, 0x7e,
};

static int s;
static uint8_t buf[1024];

static int is_rapp(const uint8_t *u, uint8_t which)
{
	return memcmp(u, rapp_uuid, UUID_VARIANT_OCTET) == 0 &&
	    u[UUID_VARIANT_OCTET] == which &&
	    memcmp(u + UUID_VARIANT_OCTET + 1, rapp_uuid + UUID_VARIANT_OCTET + 1,
	    UUID128_LEN - UUID_VARIANT_OCTET - 1) == 0;
}

static void put16(uint8_t *p, uint16_t v) { p[0] = v & 0xff; p[1] = v >> 8; }
static uint16_t get16(const uint8_t *p) { return p[0] | (p[1] << 8); }

static void tx(const uint8_t *p, size_t n)
{
	if (send(s, p, n, 0) != (ssize_t)n) { perror("send"); exit(1); }
}

/* Answers one PDU the phone initiated. */
static void answer_peer(uint8_t op, ssize_t n)
{
	uint8_t e[ATT_ERROR_RSP_LEN] = { ATT_ERROR_RSP, op, 0, 0, ATT_ERR_NOT_SUPPORTED };

	if (op == ATT_INDICATE) {
		uint8_t c = ATT_CONFIRM;
		tx(&c, 1);
		printf("  phone indication, %zd bytes, confirmed\n", n);
		return;
	}
	if (op == ATT_NOTIFY || (op & ATT_COMMAND_FLAG)) {
		printf("  phone PDU 0x%02x ignored\n", op);
		return;
	}
	if (op == ATT_MTU_REQ) {
		if (n == 3) {
			uint8_t r[3] = { ATT_MTU_RSP };
			put16(r + 1, REQUESTED_MTU);
			tx(r, sizeof r);
			printf("  phone MTU exchange answered\n");
			return;
		}
		e[4] = ATT_ERR_INVALID_PDU;
	} else if (op == ATT_FIND_INFO_REQ || op == ATT_FIND_BY_TYPE_VALUE_REQ ||
	    op == ATT_READ_BY_TYPE_REQ || op == ATT_READ_BY_GROUP_REQ) {
		if (n < ATT_ERROR_RSP_LEN) {
			e[4] = ATT_ERR_INVALID_PDU;
		} else {
			uint16_t start = get16(buf + 1), end = get16(buf + 3);
			put16(e + 2, start);
			e[4] = (start == 0 || start > end) ? ATT_ERR_INVALID_HANDLE : ATT_ERR_NOT_FOUND;
		}
	}
	tx(e, sizeof e);
	printf("  phone request 0x%02x refused (0x%02x)\n", op, e[4]);
}

/* Receives the response to our request: `want` or an Error Response. */
static ssize_t rx_rsp(uint8_t want)
{
	for (;;) {
		struct pollfd pf = { s, POLLIN, 0 };
		if (poll(&pf, 1, ATT_TRANSACTION_TIMEOUT_MS) <= 0) {
			fprintf(stderr, "timeout waiting for 0x%02x\n", want);
			exit(1);
		}
		ssize_t n = recv(s, buf, sizeof buf, 0);
		if (n <= 0) { fprintf(stderr, "recv: %zd %s\n", n, strerror(errno)); exit(1); }
		if (buf[0] == want) return n;
		if (buf[0] == ATT_ERROR_RSP) {
			if (n < ATT_ERROR_RSP_LEN) { fprintf(stderr, "short Error Response\n"); exit(1); }
			return n;
		}
		answer_peer(buf[0], n);
	}
}

static int refused(const char *what)
{
	if (buf[0] != ATT_ERROR_RSP) return 0;
	printf("%s refused: ATT error 0x%02x\n", what, buf[4]);
	return 1;
}

static void read_bootstrap(uint16_t boot, const char *when)
{
	uint8_t q[3] = { ATT_READ_REQ };
	put16(q + 1, boot);
	tx(q, sizeof q);
	ssize_t n = rx_rsp(ATT_READ_RSP);
	char what[64];
	snprintf(what, sizeof what, "bootstrap read %s", when);
	if (refused(what)) return;
	printf("bootstrap read %s: %zd bytes\n", when, n - 1);
	const char *keys[] = { "offer_id", "transports", "fi.refineid.rapp.ble",
	    "fi.refineid.stream", "retry_after_ms" };
	for (size_t i = 0; i < sizeof keys / sizeof *keys; i++)
		printf("  contains \"%s\": %s\n", keys[i],
		    memmem(buf + 1, n - 1, keys[i], strlen(keys[i])) ? "yes" : "no");
}

static void write_pairing_preamble(uint16_t chan)
{
	/* Deterministic CBOR ["rapp-ble", "pairing", h''] (section 2.2.1). */
	static const char dom[] = "rapp-ble", pur[] = "pairing";
	uint8_t pre[64], w[96];
	size_t k = 0, m = 0;

	pre[k++] = 0x83;
	pre[k++] = 0x60 | (sizeof dom - 1); memcpy(pre + k, dom, sizeof dom - 1); k += sizeof dom - 1;
	pre[k++] = 0x60 | (sizeof pur - 1); memcpy(pre + k, pur, sizeof pur - 1); k += sizeof pur - 1;
	pre[k++] = 0x40;

	/* SAR header: total length, sequence 0, SINGLE, reserved (section 5.3). */
	w[m++] = ATT_WRITE_REQ; put16(w + m, chan); m += 2;
	w[m++] = k >> 8; w[m++] = k & 0xff; w[m++] = 0; w[m++] = 0;
	w[m++] = SAR_SINGLE; w[m++] = 0;
	memcpy(w + m, pre, k); m += k;
	tx(w, m);
	rx_rsp(ATT_WRITE_RSP);
	if (refused("pairing preamble")) exit(1);
	printf("pairing preamble (%zu bytes, SAR SINGLE): accepted\n", k);
}

int main(int argc, char **argv)
{
	struct sockaddr_l2cap sa;
	uint8_t q[8];
	int pairing = 0, ch;
	ssize_t n;

	while ((ch = getopt(argc, argv, "p")) != -1) {
		if (ch != 'p') goto usage;
		pairing = 1;
	}
	argc -= optind; argv += optind;
	if (argc != 1) goto usage;

	s = socket(PF_BLUETOOTH, SOCK_SEQPACKET, BLUETOOTH_PROTO_L2CAP);
	if (s < 0) { perror("socket"); return 1; }
	memset(&sa, 0, sizeof sa);
	sa.l2cap_len = sizeof sa; sa.l2cap_family = AF_BLUETOOTH;
	sa.l2cap_bdaddr_type = BDADDR_LE_PUBLIC;
	memcpy(&sa.l2cap_bdaddr, NG_HCI_BDADDR_ANY, sizeof(bdaddr_t));
	if (bind(s, (struct sockaddr *)&sa, sizeof sa) < 0) { perror("bind"); return 1; }
	memset(&sa, 0, sizeof sa);
	sa.l2cap_len = sizeof sa; sa.l2cap_family = AF_BLUETOOTH;
	sa.l2cap_bdaddr_type = BDADDR_LE_RANDOM; sa.l2cap_cid = NG_L2CAP_ATT_CID;
	if (!bt_aton(argv[0], &sa.l2cap_bdaddr)) { fprintf(stderr, "bad address\n"); return 2; }
	if (connect(s, (struct sockaddr *)&sa, sizeof sa) < 0) { perror("connect"); return 1; }
	printf("connected (ATT fixed channel)\n");

	q[0] = ATT_MTU_REQ; put16(q + 1, REQUESTED_MTU); tx(q, 3);
	n = rx_rsp(ATT_MTU_RSP);
	if (refused("MTU exchange")) return 1;
	uint16_t server = get16(buf + 1);
	uint16_t mtu = server < REQUESTED_MTU ? server : REQUESTED_MTU;
	printf("MTU: phone %u, negotiated %u: %s\n", server, mtu,
	    mtu >= MINIMUM_MTU ? "ok" : "below 512, a requester drops the link");

	uint16_t start = 1, svc_start = 0, svc_end = 0;
	for (;;) {
		q[0] = ATT_READ_BY_GROUP_REQ; put16(q + 1, start); put16(q + 3, LAST_HANDLE);
		put16(q + 5, UUID_PRIMARY); tx(q, 7);
		n = rx_rsp(ATT_READ_BY_GROUP_RSP);
		if (buf[0] == ATT_ERROR_RSP) break;
		uint8_t len = buf[1];
		uint16_t last = 0;
		for (ssize_t i = 2; len && i + len <= n; i += len) {
			uint16_t h = get16(buf + i), e = get16(buf + i + 2);
			if (len == 4 + UUID128_LEN && is_rapp(buf + i + 4, RAPP_SERVICE)) {
				svc_start = h; svc_end = e;
				printf("RAPP service: handles 0x%04x-0x%04x\n", h, e);
			} else {
				printf("service 0x%04x-0x%04x\n", h, e);
			}
			last = e;
		}
		if (last == 0 || last == LAST_HANDLE) break;
		start = last + 1;
	}
	if (!svc_start) { printf("RAPP service not found\n"); return 1; }

	uint16_t chan = 0, boot = 0, cccd = 0;
	uint8_t chan_props = 0, boot_props = 0;
	start = svc_start;
	while (start <= svc_end) {
		q[0] = ATT_READ_BY_TYPE_REQ; put16(q + 1, start); put16(q + 3, svc_end);
		put16(q + 5, UUID_CHARACTERISTIC); tx(q, 7);
		n = rx_rsp(ATT_READ_BY_TYPE_RSP);
		if (buf[0] == ATT_ERROR_RSP) break;
		uint8_t len = buf[1];
		uint16_t last = 0;
		for (ssize_t i = 2; len && i + len <= n; i += len) {
			uint16_t decl = get16(buf + i), vh = get16(buf + i + 3);
			uint8_t props = buf[i + 2];
			if (len == 5 + UUID128_LEN && is_rapp(buf + i + 5, RAPP_CHANNEL)) { chan = vh; chan_props = props; }
			if (len == 5 + UUID128_LEN && is_rapp(buf + i + 5, RAPP_BOOTSTRAP)) { boot = vh; boot_props = props; }
			last = decl;
		}
		if (last == 0 || last == LAST_HANDLE) break;
		start = last + 1;
	}
	printf("channel: value 0x%04x properties 0x%02x (needs Write and Indicate): %s\n",
	    chan, chan_props,
	    chan && (chan_props & PROP_WRITE) && (chan_props & PROP_INDICATE) ? "ok" : "MISMATCH");
	printf("bootstrap: value 0x%04x properties 0x%02x (needs Read): %s\n",
	    boot, boot_props, boot && (boot_props & PROP_READ) ? "ok" : "MISMATCH");
	if (!chan || !boot) return 1;

	q[0] = ATT_FIND_INFO_REQ; put16(q + 1, chan + 1); put16(q + 3, svc_end); tx(q, 5);
	n = rx_rsp(ATT_FIND_INFO_RSP);
	if (buf[0] == ATT_FIND_INFO_RSP && buf[1] == 1)
		for (ssize_t i = 2; i + 4 <= n; i += 4)
			if (get16(buf + i + 2) == UUID_CCCD && !cccd) cccd = get16(buf + i);
	printf("channel CCCD: 0x%04x %s\n", cccd, cccd ? "ok" : "NOT FOUND");
	if (!cccd) return 1;

	read_bootstrap(boot, "without routing");

	q[0] = ATT_WRITE_REQ; put16(q + 1, cccd); put16(q + 3, CCCD_INDICATIONS); tx(q, 5);
	rx_rsp(ATT_WRITE_RSP);
	if (refused("CCCD write")) return 1;
	printf("CCCD write 0x%04x: ok\n", CCCD_INDICATIONS);

	if (pairing) {
		write_pairing_preamble(chan);
		read_bootstrap(boot, "after the pairing preamble");
	}

	close(s);
	printf("closed\n");
	return 0;

usage:
	fprintf(stderr, "usage: rapp_ble_probe [-p] <random-device-address>\n");
	return 2;
}
