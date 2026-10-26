# Running the bot on a server

The inactivity rule is only as accurate as the bot's uptime: messages sent while it's off are never
delivered to it. An always-on Linux server fixes that. This guide sets one up from scratch, which
takes about 20 minutes, and then keeps it updated with one command.

The commands are for **Ubuntu 24.04 LTS**. Ubuntu 22.04 and Debian 12 work the same way. Each
code block says where to run it: on the **server**, or on **your computer**, meaning the one
running the bot now.

## What you need

- **A small server:**
  - 1 vCPU, 1 GB RAM, Ubuntu 24.04 LTS.
  - **Hong Kong** is the best region. It's close to QQ, reaches DeepSeek, Steam and GitHub without a
    proxy, and needs no ICP filing (备案).
- **`config.yaml` and the `data/` folder** from your computer, plus `local/` if you keep private
  versions of styles or rules there. Only the shared code is on GitHub.
- **Admin access to the GitHub repo.** It's private, so the server gets its own read-only key
  (step 5).

## 1. Log in and install the basics

Your computer:

```bash
ssh root@SERVER_IP
```

Some providers give you an `ubuntu` user instead of `root`; use whichever they show. Then, on the
server:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip
```

Optionally, set the clock to Beijing time so the logs read the same as the bot. The bot itself
always uses Beijing time, whatever the server is set to.

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

## 2. Close everything but SSH

The bot only makes outgoing connections, so nothing needs to reach the server except your SSH
login. On the server:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

## 3. Tell QQ about the server's address (if your bot uses an IP whitelist)

On the server, find its public address:

```bash
curl -4 ifconfig.me
```

On the QQ Open Platform, open your bot → 开发设置. If there is an **IP 白名单** with addresses in it,
add this one. If calls to QQ fail with error 11298 after the move, this list is why.

## 4. Create a user for the bot

The bot runs as its own user, not as root. On the server:

```bash
sudo useradd --create-home --shell /bin/bash xiaolian
```

## 5. Give the server read-only access to the repo

On the server, become the bot's user. Every command until `exit` runs as `xiaolian`:

```bash
sudo -iu xiaolian
mkdir -p -m 700 ~/.ssh
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "xiaolian-server"
cat ~/.ssh/id_ed25519.pub
```

Copy the line that `cat` prints. On GitHub, open **KahootGenius/XiaoLianBot → Settings → Deploy
keys → Add deploy key**. Paste the line, call it `server`, and leave **Allow write access**
unticked.

Back on the server, still as `xiaolian`, check that it works:

```bash
ssh -T -o StrictHostKeyChecking=accept-new git@github.com
```

"You've successfully authenticated" means it worked; GitHub then closes the connection, which is
normal.

## 6. Download the code and install it

On the server, still as `xiaolian`:

```bash
git clone git@github.com:KahootGenius/XiaoLianBot.git
cd XiaoLianBot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .
exit
```

The tests should end with `OK`. `exit` takes you back to your own user.

## 7. Move `config.yaml`, `data/` and `local/` across

**First stop the bot on your computer** (Ctrl-C in its terminal, twice if needed), and keep it
stopped. Two copies with the same AppID answer every message twice.

Your computer, in the project folder:

```bash
COPYFILE_DISABLE=1 tar czf xiaolian-move.tgz config.yaml data local
```

If you have no `local/` folder, leave `local` off the end.

```bash
scp xiaolian-move.tgz root@SERVER_IP:
```

`COPYFILE_DISABLE=1` stops macOS from slipping hidden `._` files into the archive. Use the same
user as in step 1 instead of `root` if yours differs.

On the server, as your own user:

```bash
sudo tar xzf ~/xiaolian-move.tgz -C /home/xiaolian/XiaoLianBot
sudo chown -R xiaolian:xiaolian /home/xiaolian/XiaoLianBot/config.yaml /home/xiaolian/XiaoLianBot/data
sudo chown -R xiaolian:xiaolian /home/xiaolian/XiaoLianBot/local 2>/dev/null || true
sudo chmod 600 /home/xiaolian/XiaoLianBot/config.yaml
rm ~/xiaolian-move.tgz
```

Delete `xiaolian-move.tgz` on your computer too: it contains your keys.

`data/` holds:
- the activity history (`manager.db`)
- AI chat's conversation and members' memories (`chat.db`)
- today's teams (`teams.json`)

Without it, the bot starts counting everyone from zero.

## 8. Start it as a service

On the server:

```bash
sudo cp /home/xiaolian/XiaoLianBot/deploy/xiaolian.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xiaolian
```

The bot now starts at boot and restarts itself 10 seconds after any crash. Watch it start:

```bash
journalctl -u xiaolian -f
```

Look for `机器人「小蓮女神」启动成功`, then the `[Manager]`, `[Teams]` and `[AIChat]` lines.
Ctrl-C stops watching; the bot keeps running. Finally, @ the bot in QQ with `/管理状态`.

## Everyday use

| To | Run on the server |
|---|---|
| Update to the latest code on GitHub | `sudo /home/xiaolian/XiaoLianBot/deploy/update.sh` |
| Restart without updating, e.g. after editing `config.yaml` | `sudo systemctl restart xiaolian` |
| Edit `config.yaml` | `sudo -u xiaolian nano /home/xiaolian/XiaoLianBot/config.yaml` |
| Stop, or start again | `sudo systemctl stop xiaolian`, `sudo systemctl start xiaolian` |
| Watch the log live | `journalctl -u xiaolian -f` |
| Read the last hour of log | `journalctl -u xiaolian --since "1 hour ago"` |
| Check it's running | `systemctl status xiaolian` |

### How updates work

1. Change code, styles or rules on your computer, then commit and push to GitHub.
2. On the server, run `deploy/update.sh`. It:
   - pulls the new commits (fast-forward only)
   - reinstalls packages if `requirements.txt` changed
   - runs the tests
   - restarts the bot and shows the start of its log
3. **If the tests fail,** the script puts the code back the way it was and doesn't restart, so the
   running bot is never replaced by broken code.

Edit tracked files, meaning code, `styles/` and `prompts/`, on your computer, never on the server;
otherwise the next pull fails. `config.yaml`, `data/` and `local/` aren't tracked, so changing those
on the server is fine. Restart afterwards with `sudo systemctl restart xiaolian`.

The tests in `update.sh` check the shared files only. After changing a private style, check it with:

```bash
sudo -u xiaolian -H bash -c 'cd /home/xiaolian/XiaoLianBot && .venv/bin/python -m core.style check 小莲'
```

A mistake in a private `teams.yaml` or `achievements.yaml` stops the bot at startup;
`journalctl -u xiaolian -n 50` names it.

## The member table

The page stays on `127.0.0.1:8765` on the server, so nobody can reach it directly.

- **For you:** open an SSH tunnel from your computer:
  ```bash
  ssh -L 8765:127.0.0.1:8765 root@SERVER_IP
  ```
  Then browse to `http://127.0.0.1:8765/?token=…` on your computer while that login stays open.
- **For other admins:** a Cloudflare tunnel gives the page a public address: a random
  `https://…trycloudflare.com` one to start with, or a fixed one on your own domain
  ([A fixed address](#a-fixed-address)). Visitors still need the token, so strangers get "403".

### The member table for other admins

On the server, install `cloudflared` once. `dpkg --print-architecture` says `amd64` or `arm64`;
use that in the file name:

```bash
curl -fL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i /tmp/cloudflared.deb
```

Run the tunnel as a service, so it starts at boot and comes back if it stops. Stop any tunnel you
started by hand first (Ctrl-C in its terminal).

```bash
sudo cp /home/xiaolian/XiaoLianBot/deploy/xiaolian-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xiaolian-tunnel
```

Print the link to share, with the token already in it:

```bash
sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-link.sh
```

Send it to admins privately, never in the main group.

**The address changes whenever the tunnel restarts:** after a reboot, a crash, or
`sudo systemctl restart xiaolian-tunnel`. Run `tunnel-link.sh` again and send the new link. The
old link stops working at once. Restarting the bot or running `update.sh` doesn't change it. To stop
the changes for good, give it a fixed address (next section).

To switch the public page off: `sudo systemctl disable --now xiaolian-tunnel`.

### A fixed address

A quick tunnel's address is random, and there's no way to keep it. A named tunnel on a domain you
own keeps one address, like `https://panel.example.com`, through restarts and reboots, so you send
the link once. Cloudflare doesn't charge for the tunnel; you only pay for the domain. Do the
section above first.

1. **Put a domain on Cloudflare.** Sign up at [dash.cloudflare.com](https://dash.cloudflare.com).
   If you have no domain, buy one there under **Domain Registration** (sold at cost; a `.com` is
   about US$10 a year). A domain bought elsewhere works too: add it to Cloudflare on the Free plan
   and switch its nameservers where you bought it, as Cloudflare's page explains.
2. **Create the tunnel.** Go to **Networking** > **Tunnels** > **Create a tunnel**, name it
   `xiaolian`, and pick Debian when asked about the system. Copy the install command it shows,
   but **don't run it**: that would set up a second tunnel service next to ours.
3. **Give the token to the server.** Run this, paste the command when asked, and type the
   address you'll use:
   ```bash
   sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-fixed.sh
   ```
   It keeps only the token, in `/etc/xiaolian/tunnel.env` (readable by root only), and restarts
   the tunnel. The tunnel shows as connected in Cloudflare within a minute.
4. **Point the address at the page.** In Cloudflare, open the tunnel and its **Routes** tab, then
   **Add route** > **Published application**. Use the subdomain and domain you typed in step 3
   (for example `panel` and `example.com`), and Service URL `http://127.0.0.1:8765`. If the page
   asks for a type and a URL instead, use `HTTP` and `127.0.0.1:8765`.

From now on `tunnel-link.sh` prints `https://panel.example.com/?token=…`, and that link keeps
working. To cut off someone who shouldn't have it, change `manager.dashboard.token` in `config.yaml`
and restart the bot: every old link stops working, and `tunnel-link.sh` prints the new one.

- **The link says "403".** The token in the link is wrong; run `tunnel-link.sh` again.
- **Cloudflare shows error 1033.** The tunnel isn't connected. See
  `journalctl -u xiaolian-tunnel -n 30`. If the token was wrong, run `tunnel-fixed.sh` again.
- **Back to a random address:** `sudo /home/xiaolian/XiaoLianBot/deploy/tunnel-fixed.sh --off`.

## What AI chat remembers about members

Members see and delete their own memories in QQ with `/我的记忆` and `/忘记`. To see or fix anyone's,
use `memories.sh` on the server. Changes apply at once; the bot doesn't need a restart.

- **List everything:**
  ```bash
  sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh
  ```
- **Only memories, or members' nicknames, containing a word:**
  ```bash
  sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh 爸爸
  ```
- **Delete by number** (the first column of the list):
  ```bash
  sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh delete 12 15
  ```
- **Rewrite one:**
  ```bash
  sudo /home/xiaolian/XiaoLianBot/deploy/memories.sh edit 12 下周要考四级
  ```

## Backups

Everything except `config.yaml`, `data/` and `local/` is on GitHub. Back up `data/` now and then. For a clean
copy, stop the bot for a moment:

```bash
sudo systemctl stop xiaolian
sudo tar czf ~/xiaolian-data-$(date +%F).tgz -C /home/xiaolian/XiaoLianBot data
sudo systemctl start xiaolian
```

Then download it with `scp`. Your provider's snapshot feature works too.

## Troubleshooting

- **`systemctl status` says "start request repeated too quickly".** The bot crashed 5 times in 5
  minutes, usually because of a mistake in `config.yaml`, `prompts/*.yaml` or a style file.
  `journalctl -u xiaolian -n 50` names it. Fix it, then:
  ```bash
  sudo systemctl reset-failed xiaolian && sudo systemctl start xiaolian
  ```
- **Every command gets two replies in QQ.** Another copy is running somewhere: your computer, or a
  friend's using the same AppID. Stop it.
- **Error 11298 in the log.** The server's address isn't on the QQ IP whitelist (step 3).
- **`update.sh` says `git pull` failed.** Files were edited on the server. To see which:
  ```bash
  sudo -u xiaolian git -C /home/xiaolian/XiaoLianBot status
  ```
  To throw those edits away:
  ```bash
  sudo -u xiaolian git -C /home/xiaolian/XiaoLianBot checkout -- FILE
  ```
- **"Read-only file system" or "Permission denied" when the bot writes.** The service only lets it
  write inside `/home/xiaolian/XiaoLianBot`. If you used a different user or folder, change `User`,
  `Group` and the paths in `deploy/xiaolian.service`, copy it again, and run
  `sudo systemctl daemon-reload`.

## Moving the bot again

1. Stop it on the old machine:
   ```bash
   sudo systemctl disable --now xiaolian
   ```
2. Copy `config.yaml`, `data/` and `local/` as in step 7.
3. Start it on the new one.

Never run both at once.
