# Troubleshooting: printer shows "offline" / stops responding

Symptom this runbook covers: an AirPrint client (macOS/iOS) reports the
Papyrus printer as **offline** while trying to print — possibly recovering
when idle — and/or printing stays dead until the container is bounced, while
the web UI keeps working fine.

Papyrus's cupsd listens on TCP **6310** (not 631) and is advertised by a
static Avahi `_ipp._tcp` record; the web app is a separate uvicorn process,
which is why the UI can be healthy while printing is not.

**Capture the evidence below BEFORE restarting the container** — a bounce
destroys the failure state and resets `/etc/cups` (printers.conf lives in the
container's writable layer).

## On the docker host

```bash
# Are all three daemons alive? (avahi-daemon, cupsd -f, uvicorn)
docker exec papyrus ps aux | grep -E 'cupsd|avahi|uvicorn' | grep -v grep

# Queue state — every queue should be "idle. enabled"
docker exec papyrus lpstat -r
docker exec papyrus lpstat -p -l
docker exec papyrus lpstat -o

# cupsd's own log (LogLevel info: shows denials, aborts, "Too many active clients")
docker exec papyrus tail -n 200 /var/log/cups/error_log

# Connection pressure: cupsd caps clients (MaxClients 200 — TCP + unix socket combined)
docker exec papyrus sh -c 'ls /proc/$(pidof cupsd)/fd | wc -l'
ss -tn | grep -c ':6310'

# Has supervision been restarting the container? (a rising count = cupsd or
# avahi died and the entrypoint exited on purpose; the pre-restart log has the
# "FATAL: ... exited unexpectedly" line)
docker inspect papyrus --format '{{.RestartCount}}'
docker logs --tail 100 papyrus

# Competing mDNS responders on the host (network_mode: host means the
# container's avahi shares UDP 5353 with anything the host runs)
ss -ulnp | grep 5353
systemctl status avahi-daemon systemd-resolved --no-pager 2>/dev/null | head -20
```

## On the failing Mac (while the queue shows offline)

```bash
# Same subnet as the server? cupsd only allows @LOCAL (the server's own subnets)
ipconfig getifaddr en0

# Raw TCP reachability of the IPP port
nc -vz <docker-host-ip> 6310

# Does the Bonjour advert still resolve? (Ctrl-C after output)
dns-sd -L "Papyrus @ <docker-hostname>" _ipp._tcp local.

# Does cupsd answer IPP at all?
ipptool -tv ipp://<docker-host-ip>:6310/printers/Papyrus get-printer-attributes.test | head -30

# What operation is actually failing? (then print once, read, and disable again)
sudo cupsctl --debug-logging
grep -E 'Unable|offline|failed|40[13]' /private/var/log/cups/error_log | tail -40
sudo cupsctl --no-debug-logging
```

## Interpreting the results

| Observation | Meaning | Next step |
|---|---|---|
| cupsd or avahi missing from `ps` | Daemon died. The entrypoint supervises both and exits so compose restarts the container — if it's still running with a dead daemon, the supervision path failed; grab `docker logs`. | File the pre-death error_log lines |
| cupsd fd count near 200 / error_log says "Too many active clients" | Client-slot exhaustion — the app side is leaking CUPS connections | Capture `docker exec papyrus ss -xn \| grep -c cups.sock` and open an issue with the numbers |
| `nc` refused/timeout but host `ss -tlnp` shows 6310 listening | Network path between Mac and host (VLAN ACL, AP client isolation) | Fix the network, not Papyrus |
| `ipptool` returns 403 | Mac's subnet isn't in cupsd's `@LOCAL` (different VLAN) | Add an `Allow from <lan-cidr>` to `<Location />` in `docker/cups/cupsd.conf` and rebuild |
| `dns-sd` fails to resolve, avahi alive | mDNS conflict — host-level avahi/systemd-resolved competing on 5353 | Disable the host responder (`systemctl disable --now avahi-daemon`, or set `MulticastDNS=no` for systemd-resolved) |
| Queue shows "disabled" in `lpstat -p -l` | cupsd stopped the queue; startup re-enables it on every boot — seeing it mid-run means a new stop event | error_log has the reason (LogLevel info) |
| Mac error_log shows a 401 on Send-Document/Cancel-Job | The `Require user @OWNER @SYSTEM` policy rejected the client's requesting-user-name | See TODO.md (tracked); relax the policy group |
