# rapp-ble-probe

A FreeBSD probe of a phone's RAPP GATT server (`fi.refineid.rapp.ble`,
RAPP v26.11.2 section 5), independent of the RefineID Rust stack. It
connects to one advertiser and prints each step of the section 5.2 setup:
the ATT MTU, the RAPP service and its characteristics' properties, the
channel's CCCD, a bootstrap read without routing, and enabling
indications. With `-p` it also writes the `"pairing"` routing preamble and
reads the bootstrap characteristic, which serves the pairing offer or
`pairing-busy` while the phone is in pairing mode over Bluetooth. Reading
the offer starts no CPace and spends no pairing attempt.

It prints no device address, and it answers what the phone initiates on
the link (indications, an MTU exchange, discovery requests) so the
phone's ATT transaction timer never stalls it.

## Build and run (FreeBSD)

LE must be enabled on the adapter (refineid-unix
`doc/install-freebsd.md`, "Bluetooth"). Then, as a normal user:

```sh
cc -O2 -Wall -Wextra -o rapp_ble_probe rapp_ble_probe.c -lbluetooth

# Find an advertiser of the RAPP service UUID; keep the RefineID app in
# the foreground with remote access on.
hccontrol -n ubt0hci le_scan -n 150 -a -v > scan.txt
hccontrol -n ubt0hci le_set_scan_enable disable
addr=$(awk '/BD_ADDR/ { a = $2 }
    /7e39fd01-a6b5-4d78-9e11-37e28e9545f1/ { print a }' scan.txt | tail -1)

./rapp_ble_probe "$addr"
./rapp_ble_probe -p "$addr"    # while the phone shows a pairing code
```

The address is a random one the phone rotates; scan again when the
connection fails. If `le_scan` reports `Command disallowed`, a scan is
still enabled: run the `le_set_scan_enable disable` line and retry.
