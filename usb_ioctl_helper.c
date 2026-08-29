/*
 * usb_ioctl_helper.c
 *
 * Minimal POSIX-C + Linux USBFS ioctl helper for shell scripts.
 * - Opens a /dev/bus/usb/... node
 * - Can claim/release an interface
 * - Can run USB control + bulk transfers via USBDEVFS ioctls
 *
 * Build (Linux):
 *   cc -O2 -Wall -Wextra -o usb_ioctl_helper usb_ioctl_helper.c
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <linux/usb/ch9.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static void
usage(const char *prog)
{
  fprintf(stderr,
          "Usage:\n"
          "  %s --path DEVICE --claim IFACE\n"
          "  %s --path DEVICE --release IFACE\n"
          "  %s --path DEVICE [--interface IFACE] control-in BMRequestType BRequest WValue WIndex WLength [TimeoutMs]\n"
          "  %s --path DEVICE [--interface IFACE] control-out BMRequestType BRequest WValue WIndex DATAHEX [TimeoutMs]\n"
          "  %s --path DEVICE [--interface IFACE] bulk-in EP Length [TimeoutMs]\n"
          "  %s --path DEVICE [--interface IFACE] bulk-out EP DATAHEX [TimeoutMs]\n"
          "  %s --path DEVICE [--interface IFACE] bulk-xchange OUT_EP IN_EP DATAHEX READLEN [TimeoutMs]\n"
          "\n"
          "HEX numeric arguments accept 0x-prefixed or decimal.\n"
          "DATAHEX is hex bytes with no spaces, e.g. 00A4040000.\n",
          prog, prog, prog, prog, prog, prog, prog);
}

static int
parse_u32(const char *s, uint32_t *v)
{
  char *endptr = NULL;
  unsigned long parsed;

  errno = 0;
  parsed = strtoul(s, &endptr, 0);
  if (errno != 0 || endptr == s || *endptr != '\0') {
    return -1;
  }
  *v = (uint32_t)parsed;
  return 0;
}

static int
hex_to_bytes(const char *hex, unsigned char **out, unsigned int *len)
{
  unsigned int i;
  unsigned int out_len;
  unsigned int c;
  unsigned int bytev;
  unsigned char *buf;

  if (hex == NULL || out == NULL || len == NULL) {
    return -1;
  }

  if (hex[0] == '\0') {
    *out = NULL;
    *len = 0;
    return 0;
  }

  out_len = strlen(hex);
  if ((out_len & 1U) != 0U) {
    return -1;
  }

  buf = (unsigned char *)calloc(1, (out_len / 2U) + 1U);
  if (buf == NULL) {
    return -1;
  }

  for (i = 0; i < (out_len / 2U); i++) {
    if (sscanf(hex + (i * 2U), "%2x", &bytev) != 1) {
      free(buf);
      return -1;
    }
    c = (unsigned int)bytev;
    if (c > 0xFFU) {
      free(buf);
      return -1;
    }
    buf[i] = (unsigned char)c;
  }

  *out = buf;
  *len = out_len / 2U;
  return 0;
}

static void
dump_hex(const unsigned char *data, int len)
{
  int i;
  for (i = 0; i < len; i++) {
    printf("%02X", (unsigned int)data[i]);
  }
  printf("\n");
}

static int
set_interface(int fd, int iface, bool claim)
{
  unsigned int ifno = (unsigned int)iface;
  int req = claim ? USBDEVFS_CLAIMINTERFACE : USBDEVFS_RELEASEINTERFACE;
  const char *name = claim ? "claim" : "release";
  int rc;

  rc = ioctl(fd, req, &ifno);
  if (rc < 0) {
    fprintf(stderr, "%s: ioctl(%s interface=%d) failed: %s\n",
            name, name, iface, strerror(errno));
    return -1;
  }
  return 0;
}

static int
do_control(int fd, uint32_t bm, uint32_t brq, uint32_t wvalue, uint32_t widx, unsigned char *data,
           uint32_t len, unsigned int timeout_ms)
{
  struct usbdevfs_ctrltransfer transfer;
  int rc;

  memset(&transfer, 0, sizeof(transfer));
  transfer.bRequestType = (uint8_t)bm;
  transfer.bRequest = (uint8_t)brq;
  transfer.wValue = (uint16_t)wvalue;
  transfer.wIndex = (uint16_t)widx;
  transfer.wLength = (uint16_t)len;
  transfer.timeout = timeout_ms;
  transfer.data = data;
  rc = ioctl(fd, USBDEVFS_CONTROL, &transfer);
  if (rc < 0) {
    fprintf(stderr, "control: ioctl failed: %s\n", strerror(errno));
    return -1;
  }
  if (rc == 0) {
    return 0;
  }

  if ((bm & USB_DIR_IN) != 0U) {
    dump_hex(data, rc);
  } else {
    fprintf(stdout, "%d\n", rc);
  }
  return (int)rc;
}

static int
do_bulk(int fd, uint32_t ep, unsigned char *buf, uint32_t len, unsigned int timeout_ms, bool output_hex)
{
  struct usbdevfs_bulktransfer transfer;
  int rc;

  memset(&transfer, 0, sizeof(transfer));
  transfer.ep = (unsigned char)ep;
  transfer.len = (uint32_t)len;
  transfer.timeout = timeout_ms;
  transfer.data = buf;

  rc = ioctl(fd, USBDEVFS_BULK, &transfer);
  if (rc < 0) {
    fprintf(stderr, "bulk: ioctl failed on ep 0x%02x: %s\n", (unsigned int)ep, strerror(errno));
    return -1;
  }

  if (output_hex) {
    dump_hex(buf, rc);
  } else {
    fprintf(stdout, "%d\n", rc);
  }
  return rc;
}

int
main(int argc, char **argv)
{
  const char *path = NULL;
  const char *op = NULL;
  int iface = -1;
  unsigned int timeout = 1000U;
  int i = 1;
  int fd;
  int rc;

  if (argc < 2) {
    usage(argv[0]);
    return 64;
  }

  while (i < argc) {
    if (strcmp(argv[i], "--path") == 0) {
      if (i + 1 >= argc) {
        usage(argv[0]);
        return 64;
      }
      path = argv[i + 1];
      i += 2;
      continue;
    }
    if (strcmp(argv[i], "--interface") == 0) {
      uint32_t v;
      if (i + 1 >= argc || parse_u32(argv[i + 1], &v) != 0 || v > 255U) {
        usage(argv[0]);
        return 64;
      }
      iface = (int)v;
      i += 2;
      continue;
    }
    if (strcmp(argv[i], "--timeout") == 0) {
      uint32_t v;
      if (i + 1 >= argc || parse_u32(argv[i + 1], &v) != 0) {
        usage(argv[0]);
        return 64;
      }
      timeout = (unsigned int)v;
      i += 2;
      continue;
    }

    op = argv[i];
    i++;
    break;
  }

  if (path == NULL || op == NULL) {
    usage(argv[0]);
    return 64;
  }
  if (argc - i < 1) {
    usage(argv[0]);
    return 64;
  }

  fd = open(path, O_RDWR);
  if (fd < 0) {
    fprintf(stderr, "open %s failed: %s\n", path, strerror(errno));
    return 1;
  }

  if (iface >= 0) {
    rc = set_interface(fd, iface, true);
    if (rc != 0) {
      close(fd);
      return 1;
    }
  }

  if (strcmp(op, "claim") == 0) {
    if (iface < 0 || i >= argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    uint32_t iface_arg;
    if (parse_u32(argv[i], &iface_arg) != 0 || iface_arg > 255U) {
      fprintf(stderr, "invalid interface: %s\n", argv[i]);
      close(fd);
      return 64;
    }
    rc = set_interface(fd, (int)iface_arg, true);
    close(fd);
    return rc == 0 ? 0 : 1;
  }

  if (strcmp(op, "release") == 0) {
    if (iface < 0 || i >= argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    uint32_t iface_arg;
    if (parse_u32(argv[i], &iface_arg) != 0 || iface_arg > 255U) {
      fprintf(stderr, "invalid interface: %s\n", argv[i]);
      close(fd);
      return 64;
    }
    rc = set_interface(fd, (int)iface_arg, false);
    close(fd);
    return rc == 0 ? 0 : 1;
  }

  if (strcmp(op, "control-in") == 0) {
    uint32_t bm, brq, wvalue, windex, wlen;
    unsigned char *buf = NULL;
    if (i + 5 > argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    if (parse_u32(argv[i], &bm) != 0 || parse_u32(argv[i + 1], &brq) != 0 ||
        parse_u32(argv[i + 2], &wvalue) != 0 || parse_u32(argv[i + 3], &windex) != 0 ||
        parse_u32(argv[i + 4], &wlen) != 0 || wlen == 0U || wlen > 65535U) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    i += 5;
    if (i < argc) {
      uint32_t tout;
      if (parse_u32(argv[i], &tout) == 0) {
        timeout = (unsigned int)tout;
      }
    }

    buf = (unsigned char *)calloc(1, (size_t)wlen);
    if (buf == NULL) {
      fprintf(stderr, "alloc failed\n");
      close(fd);
      return 1;
    }
    rc = do_control(fd, bm, brq, wvalue, windex, buf, wlen, timeout);
    free(buf);
    if (iface >= 0) {
      set_interface(fd, iface, false);
    }
    close(fd);
    if (rc < 0) {
      return 1;
    }
    return 0;
  }

  if (strcmp(op, "control-out") == 0) {
    uint32_t bm, brq, wvalue, windex;
    unsigned char *buf = NULL;
    uint32_t blen;
    if (i + 5 > argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    if (parse_u32(argv[i], &bm) != 0 || parse_u32(argv[i + 1], &brq) != 0 ||
        parse_u32(argv[i + 2], &wvalue) != 0 || parse_u32(argv[i + 3], &windex) != 0 ||
        hex_to_bytes(argv[i + 4], &buf, &blen) != 0) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    i += 5;
    if (i < argc) {
      uint32_t tout;
      if (parse_u32(argv[i], &tout) == 0) {
        timeout = (unsigned int)tout;
      }
    }

    rc = do_control(fd, bm, brq, wvalue, windex, buf, blen, timeout);
    free(buf);
    if (iface >= 0) {
      set_interface(fd, iface, false);
    }
    close(fd);
    if (rc < 0) {
      return 1;
    }
    return 0;
  }

  if (strcmp(op, "bulk-in") == 0) {
    uint32_t ep, blen;
    unsigned char *buf;
    if (i + 2 > argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    if (parse_u32(argv[i], &ep) != 0 || parse_u32(argv[i + 1], &blen) != 0 || blen == 0U) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    i += 2;
    if (i < argc) {
      uint32_t tout;
      if (parse_u32(argv[i], &tout) == 0) {
        timeout = (unsigned int)tout;
      }
    }
    buf = (unsigned char *)calloc(1, (size_t)blen);
    if (buf == NULL) {
      fprintf(stderr, "alloc failed\n");
      close(fd);
      return 1;
    }
    rc = do_bulk(fd, ep, buf, blen, timeout, true);
    free(buf);
    if (iface >= 0) {
      set_interface(fd, iface, false);
    }
    close(fd);
    if (rc < 0) {
      return 1;
    }
    return 0;
  }

  if (strcmp(op, "bulk-out") == 0) {
    uint32_t ep;
    unsigned char *buf = NULL;
    unsigned int blen;
    if (i + 2 > argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    if (parse_u32(argv[i], &ep) != 0 ||
        hex_to_bytes(argv[i + 1], &buf, &blen) != 0) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    i += 2;
    if (i < argc) {
      uint32_t tout;
      if (parse_u32(argv[i], &tout) == 0) {
        timeout = (unsigned int)tout;
      }
    }
    rc = do_bulk(fd, ep, buf, blen, timeout, false);
    free(buf);
    if (iface >= 0) {
      set_interface(fd, iface, false);
    }
    close(fd);
    if (rc < 0) {
      return 1;
    }
    return 0;
  }

  if (strcmp(op, "bulk-xchange") == 0) {
    uint32_t ep_out;
    uint32_t ep_in;
    uint32_t read_len;
    unsigned char *out_data = NULL;
    unsigned int out_len;
    unsigned char *in_data = NULL;

    if (i + 4 > argc) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    if (parse_u32(argv[i], &ep_out) != 0 || parse_u32(argv[i + 1], &ep_in) != 0 ||
        hex_to_bytes(argv[i + 2], &out_data, &out_len) != 0 ||
        parse_u32(argv[i + 3], &read_len) != 0 || read_len == 0U) {
      usage(argv[0]);
      close(fd);
      return 64;
    }
    i += 4;
    if (i < argc) {
      uint32_t tout;
      if (parse_u32(argv[i], &tout) == 0) {
        timeout = (unsigned int)tout;
      }
    }

    rc = do_bulk(fd, ep_out, out_data, out_len, timeout, false);
    if (rc < 0) {
      free(out_data);
      close(fd);
      return 1;
    }

    in_data = (unsigned char *)calloc(1, (size_t)read_len);
    if (in_data == NULL) {
      fprintf(stderr, "alloc failed\n");
      free(out_data);
      close(fd);
      return 1;
    }
    rc = do_bulk(fd, ep_in, in_data, read_len, timeout, true);
    free(out_data);
    free(in_data);
    if (iface >= 0) {
      set_interface(fd, iface, false);
    }
    close(fd);
    if (rc < 0) {
      return 1;
    }
    return 0;
  }

  fprintf(stderr, "unknown operation: %s\n", op);
  usage(argv[0]);
  if (iface >= 0) {
    set_interface(fd, iface, false);
  }
  close(fd);
  return 64;
}
