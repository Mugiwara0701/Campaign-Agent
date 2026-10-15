# Setting Up Tailscale on the Campaign Agent and Marketing Agent Machines

Step by step: put the two office machines on one private Tailscale network (a "tailnet"), so the Marketing Agent (MA) can
reach the Campaign Agent (CA) by name, with no fixed IP address, and nothing else can. The reason and the application
side are in [ca-ma-link.md](ca-ma-link.md); this page is only the setup.

Written from knowledge of Tailscale's tools, **not yet run on your machines**. Commands and menu names change between
Tailscale versions: where a step does not match what you see, check the current guide at tailscale.com/kb and fix this page.
The steps marked **Check** are the ones to verify first.

## What you will have at the end

```text
Tailscale account (tailnet)
 ├── campaign-agent   tag:ca   the CA machine   (listens on port 8787, only on its Tailscale address)
 └── marketing-agent  tag:ma   the MA machine   (may open connections to campaign-agent:8787, nothing else)
```

Both machines keep working if the router restarts or the LAN addresses change, because the application uses the names.
Your laptop does **not** need Tailscale: it keeps using SSH to the office machines over the LAN, as `deploy/remote.sh` does today.

## 0. Before you start

You need:

- Administrator (sudo) access on both office machines. The CA's service account has no sudo on purpose, so every
  Tailscale install step below is done as the administrator, not as the service account.
- SSH access to both machines from the laptop (`ssh admin@<office-host>`). Use `ssh -t` for commands that ask for a password.
- A Tailscale account. Use an account you control long term (a shared company login or the owner's), because the account owner
  can see and remove the machines. Do not use a throwaway account.
- About 30 minutes, and both machines powered on and online.

Names used below: the CA machine is `campaign-agent`, the MA machine is `marketing-agent`. These are Tailscale names and
do not have to match the Linux hostname.

**Secrets.** An auth key (section 3) lets any machine join your tailnet. Do not paste it into git, a `.env` file, a chat or a
document. Put it only in the command that uses it, and it can be set to expire in a day, which is enough.

## 1. Create the tailnet

1. Go to login.tailscale.com and sign in with your chosen identity provider. The first sign-in creates the tailnet.
2. Open **Settings** and note the **tailnet name** (something like `tail1234.ts.net`). Every machine will be
   `<name>.<tailnet>.ts.net`. You need this for the application's URL.
3. Open the **DNS** page and make sure **MagicDNS** is enabled.

## 2. Tags and access rules (do this before adding machines)

Tags give each machine a role, and the rules say which role may talk to which.

1. Open **Access controls** and replace the file with the following. This removes the default "everything can talk to
   everything" rule and allows exactly one connection: from the MA machine to port 8787 of the CA machine. The `tests` section makes
   Tailscale refuse to save the file if the rules do not do what we expect.

   ```json
   {
     "tagOwners": {
       "tag:ca": ["autogroup:admin"],
       "tag:ma": ["autogroup:admin"]
     },
     "acls": [
       { "action": "accept", "src": ["tag:ma"], "dst": ["tag:ca:8787"] }
     ],
     "tests": [
       { "src": "tag:ma", "accept": ["tag:ca:8787"], "deny": ["tag:ca:22", "tag:ca:5432"] },
       { "src": "tag:ca", "deny": ["tag:ma:22", "tag:ma:8787"] }
     ]
   }
   ```

2. Save. If you get an error, read it: usually a typo in a tag name.

If you later want to reach the machines over Tailscale from your own laptop (for SSH when you are away from the office),
add the laptop with a `tag:admin` tag and a rule from `tag:admin` to `tag:ca:22` and `tag:ma:22`. Not needed for the link.

## 3. Create two auth keys

1. Open **Settings → Keys → Generate auth key**.
2. For the CA machine: **Reusable** off, **Ephemeral** off, **Pre-approved** on (if shown), expiry 1 day, **Tags**: `tag:ca`.
3. Generate it and copy it (it starts with `tskey-auth-`). It is shown once.
4. Repeat for the MA machine with **Tags**: `tag:ma`.

Keep both keys in a password manager until used. They stop working after one use or one day. Do not reuse one key for both
machines: the tag on the key is what gives each machine its role.

## 4. Install on the CA machine

Run these on the office machine as the administrator. From the laptop: `ssh -t admin@<ca-host>` and then the commands.

1. Install and start the service:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo systemctl enable --now tailscaled
   ```

   The script adds Tailscale's package repository and installs the `tailscale` package. **Check:** `tailscale version`.
   If you do not want to pipe a script to a shell, use the manual repository steps on tailscale.com/kb/1031/install-linux.

2. Join the tailnet with the auth key and the name:

   ```bash
   sudo tailscale up --auth-key=tskey-auth-XXXXXXXX --hostname=campaign-agent --advertise-tags=tag:ca --ssh=false
   ```

   `--advertise-tags` must name the same tag as the key. `--ssh=false` leaves Tailscale's own SSH feature off; you keep
   using normal SSH.

3. Check:

   ```bash
   tailscale status
   tailscale ip -4          # prints the machine's 100.x.y.z address
   ```

   The machine should appear in `tailscale status` and in the admin console under **Machines** with the tag `tag:ca`.

4. In the admin console, open the machine's menu, **Disable key expiry** if it is not already off (tagged machines usually do
   not expire; confirm). If the machine's node key expires it is logged out and the link breaks, usually after about six months.

5. Allow the link port on the Tailscale interface only (the firewall on the CA machine is `ufw`):

   ```bash
   sudo ufw allow in on tailscale0 to any port 8787 proto tcp
   sudo ufw status verbose
   ```

   Do **not** run `ufw allow 8787` without `in on tailscale0`: that would open the port on the LAN too. If `ufw` is not
   enabled, enable it only after `sudo ufw allow OpenSSH`, or you will lock yourself out of SSH.

6. The CA service account (no sudo) only needs to read the Tailscale address. **Check** that it can, as that account:
   `tailscale ip -4`. If it is refused, the listener can take the address from `ip -4 addr show tailscale0` instead; note
   which one works, because the CA listener (a later step) has to use the one that does.

## 5. Install on the MA machine

The same, with the other key, the other name and the other tag, and **no** firewall rule (nothing listens on this machine):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo systemctl enable --now tailscaled
sudo tailscale up --auth-key=tskey-auth-YYYYYYYY --hostname=marketing-agent --advertise-tags=tag:ma --ssh=false
tailscale status
```

Disable key expiry for this machine in the console too. The MA machine already runs Chrome in a desktop session; Tailscale
runs as a system service and does not need that session.

## 6. Check the connection

From the **MA machine**:

```bash
tailscale status                       # campaign-agent is listed
tailscale ping campaign-agent          # replies; "via ..." shows a direct LAN path or a relay (DERP)
getent hosts campaign-agent            # resolves to the 100.x address (MagicDNS works)
getent hosts campaign-agent.<tailnet>.ts.net
```

If the short name does not resolve but the full name does, use the **full name** in the application URL (section 8). That is
what `MA_LINK_URL` should hold anyway.

Test the port **before** the CA listener exists, with a throwaway server on the CA machine (as the administrator):

```bash
cd "$(mktemp -d)"                                  # an empty folder, so nothing is served
python3 -m http.server 8787 --bind "$(tailscale ip -4)"
```

Then from the MA machine, in another terminal:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://campaign-agent:8787/     # 200
```

Stop the test server with Ctrl+C on the CA machine. Then check what must **not** work:

- From the CA machine, `curl --max-time 5 http://marketing-agent:8787/` and `tailscale ping marketing-agent` followed by a
  TCP test: the connection must fail or time out (the access rules forbid it; `tailscale ping` itself may still answer).
- From the MA machine, `curl --max-time 5 http://campaign-agent:22/` must not connect (only 8787 is allowed).
- From any other device on the same LAN (a phone, your laptop without Tailscale): `curl --max-time 5 http://<CA LAN address>:8787/`
  must not connect, because the port is only open on `tailscale0`.

If any of these succeeds, stop and fix the rules before continuing.

## 7. Keep it running

- `tailscaled` is a system service, enabled at boot (`systemctl is-enabled tailscaled` prints `enabled`). It does not
  need anyone logged in.
- Both machines should get the same Tailscale name for life. If you rename a machine in the console, update `MA_LINK_URL`.
- Updates: `sudo apt update && sudo apt install --only-upgrade tailscale`. A few minutes of link downtime during an update is fine;
  the MA runs its cached campaign and queues events meanwhile.

## 8. What the applications need

These are set in the `.env` files **by the application steps** in [ca-ma-link.md](ca-ma-link.md), after the listener exists.
Listed here so you know what to carry over from this page:

| Machine | Key | Value |
|---|---|---|
| CA | `MA_LINK_ENABLED` | `true` |
| CA | `MA_LINK_PORT` | `8787` |
| CA | `MA_LINK_TOKEN` | a random secret, see below |
| MA | `MA_LINK_URL` | `http://campaign-agent.<tailnet>.ts.net:8787/ma/v1` |
| MA | `MA_LINK_TOKEN` | the same random secret |

Make the token on the office machine, not in a chat, and copy it to both `.env` files with `push-env` (never print it):

```bash
openssl rand -base64 48        # run once on a machine you trust; the output is the token
```

## 9. The router restart drill (do it once)

The point of the whole design. With both machines up and `tailscale ping` working:

1. Note the time and the output of `tailscale ping campaign-agent` on the MA machine.
2. Restart the office router (or unplug it for a minute).
3. Every 30 seconds on the MA machine run `tailscale status` and `tailscale ping campaign-agent`.
4. Write down how long until the ping works again, and whether the LAN addresses changed (`hostname -I` before and after).
5. Put the measured time in [runbook.md](runbook.md). It is the first real number for this link.

Expected, **not measured**: it comes back by itself within a few minutes, and nothing needs to be edited. If it does not,
see the troubleshooting table.

## 10. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `tailscale up` says the key is invalid or expired | Auth keys last one use or one day. Make a new one (section 3) |
| `requested tags [tag:ca] are invalid or not permitted` | The tag is missing from `tagOwners` in the access rules, or you are not an admin. Fix section 2 |
| Machine is in the console without a tag | It joined without a tagged key. Remove it from the console, `sudo tailscale logout`, and join again with the key |
| `tailscale status` says `Stopped` or `Logged out` | `sudo systemctl start tailscaled`, then `sudo tailscale up` again with a new key if needed |
| Name does not resolve | MagicDNS is off (section 1), or the machine ignores it: `tailscale up --accept-dns=true` (the default). Use the full `.ts.net` name |
| `tailscale ping` works but `curl` times out | The access rules, the `ufw` rule, or the listener is not running or is bound to the wrong address. Check the console's access preview, `sudo ufw status`, and `ss -ltn \| grep 8787` on the CA machine |
| `tailscale ping` shows `via DERP(...)` and never `direct` | The path goes through Tailscale's relay. It works, only slower. Usually fine inside one office; check that the router does not isolate wireless clients |
| Works, then stops after months | Key expiry. Disable it for both machines (sections 4 and 5) and re-join with a new key |
| Service account cannot run `tailscale ip -4` | Use `ip -4 addr show tailscale0`, as noted in section 4 step 6 |
| Link still fails after the router restarts | `systemctl is-enabled tailscaled`, then `sudo systemctl restart tailscaled` on both machines, then `tailscale status` |

## 11. Changing or removing things

- **Rotate or revoke the link token:** change `MA_LINK_TOKEN` on both machines (see [ca-ma-link.md](ca-ma-link.md) for the rollover value).
- **Replace a machine:** in the console, remove the old machine, then follow section 4 or 5 on the new one with a fresh key.
- **A machine is lost or stolen:** remove it in the console at once. That cuts it off from the tailnet. Then rotate the link token.
- **Remove Tailscale from a machine:** `sudo tailscale down && sudo apt remove tailscale` and remove it in the console. The CA and MA
  keep working through Supabase (the fallback in [ca-ma-link.md](ca-ma-link.md)), only the direct link and blocker events stop.

## 12. Checklist

- [ ] Tailnet created, MagicDNS on, tailnet name noted
- [ ] Access rules saved with the `tests` passing
- [ ] Two tagged auth keys made (not shared, not saved anywhere permanent)
- [ ] CA machine joined as `campaign-agent` with `tag:ca`, key expiry disabled
- [ ] MA machine joined as `marketing-agent` with `tag:ma`, key expiry disabled
- [ ] `ufw` allows 8787 on `tailscale0` only
- [ ] `tailscale ping` works from the MA machine; the throwaway test server answers `200`
- [ ] The three "must not work" checks fail as they should
- [ ] `tailscaled` is enabled at boot on both machines
- [ ] Router restart drill done and the time written into the runbook
- [ ] Token generated and copied to both `.env` files (never printed or pasted)
