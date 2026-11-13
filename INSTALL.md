# Installing the YouTube plugin on a Farmhand that doesn't have it

The plugin is a Docker container of its own, on Farmhand's machine or any machine on the network that can reach
Farmhand (port 4545) and the cameras.

The addresses here are examples (`192.168.10.240` the NAS, `192.168.10.90` Farmhand, `192.168.10.91` the plugin's
machine). Use your own.

## 1. Prepare the machine

Install **Docker** and **git** (Docker builds the plugin straight from GitHub). Nothing else: the plugin connects to
the NAS itself once you give it the address in Farmhand (section 6), so no shares need mounting on the machine.

**On a Proxmox container (LXC)**, it must be allowed to mount network shares, as Farmhand's own container is. On the
Proxmox host, in `/etc/pve/lxc/<id>.conf`:

```
features: nesting=1,mount=cifs;nfs
lxc.apparmor.profile: unconfined
```

then restart the container. (A virtual machine or an ordinary computer needs nothing more.)

## 2. Get the compose file

```bash
sudo mkdir -p /opt/farmhand-stream && cd /opt/farmhand-stream
```

```bash
sudo curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/The-Original-Reth/farmhand-youtube-plugin/main/docker-compose.yml
```

It's ready as it is. It already gives the plugin's container what it needs to connect to the NAS itself
(`cap_add: SYS_ADMIN, DAC_READ_SEARCH` and `security_opt: apparmor:unconfined`, as Farmhand's own container has).

Only for an **NVIDIA card**, edit it and uncomment the `devices:` and library lines (driver set-up: the
[README](README.md#an-nvidia-card), "An NVIDIA card"):

```bash
sudo nano /opt/farmhand-stream/docker-compose.yml
```

## 3. Start it

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --build
```

It answers at `http://<that machine>:8080`, but doesn't know Farmhand yet.

## 4. Add it in Farmhand

1. Open **Plugins** (left-hand menu, under Settings).
2. Under **Add a plugin**, type its address, e.g. `http://192.168.10.91:8080`, and press **Look at it**.
3. Farmhand lists what it asks to read:
   - `cameras:read`: your printers' camera addresses
   - `prints:read`: what each printer is printing
   - `stream:migrate`: only for a Farmhand that once had the built-in Stream page; untick it to start fresh
4. Leave **Only accept its token from its own address** ticked (the token is then useless anywhere else).
5. Press **Add YouTube Stream**. The window **"Give YouTube Stream its token"** shows:
   ```
   FARMHAND_URL=http://192.168.10.90:4545/plugin-api/v1
   FARMHAND_TOKEN=Abc123…   (a long random string)
   ```
6. Press **Copy**. **Farmhand shows the token only this once** (it keeps just a scrambled copy). Only **Done** closes
   the window, so leave it open until section 5 is done.

`FARMHAND_URL` is the address your browser used for Farmhand: the plugin's machine must be able to reach it (Farmhand's
LAN IP, say). With the plugin in Docker on Farmhand's own machine, `http://api:4545/plugin-api/v1` works too.

## 5. Give the plugin its token

Paste the two lines into `stream.env`, beside `docker-compose.yml`:

```bash
sudo nano /opt/farmhand-stream/stream.env
```

Restart the plugin so it reads them, then press **Done** in Farmhand:

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --force-recreate
```

**Lost the token, or closed the window too soon?** On the Plugins page, press **New token…** on YouTube Stream, then
**Make a new token**. The old one stops working at once and the same window shows the new one: put it in `stream.env`
and restart as above.

## 6. Set it up

**1. Where it saves.** In Farmhand, open **Settings**. Under the **YouTube Stream** heading:

1. **NAS address:** the save location on your NAS, written as Windows writes it, e.g.
   `\\192.168.10.240\3d_Printing_Elves\Farmhand` (`//192.168.10.240/3d_Printing_Elves/Farmhand` works too).
2. **Username** and **Password** for the share (username blank for guest access).
3. Press **Use this location**. It says **connected**, or why it couldn't (a wrong password, a NAS it can't reach…).

The plugin makes two folders inside it, listed below the box as people reach them on the network:

- **Stream folder** `…\Farmhand\stream`: the stream's pictures, slideshows and music.
- **YouTube clips** `…\Farmhand\YouTube clips`: where **Save clip** puts its videos.

**Change…** beside either one puts it in another folder of the save location (go into folders, make a new one, **Use
this folder**). The password is kept by the plugin and never shown again. Until a save location is set, the plugin
uses `./media` and `./clips` beside its compose file.

**2. Going live.** **YouTube** is now in Farmhand's menu, after Monitor. There: sign in to YouTube (**YouTube
settings**) or paste a stream key, choose the grid and cameras, and press **Go live**.

**3. Clips.** On the YouTube page, type a length (1 second to 5 minutes) and press **Save clip**.

## Updating

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --build
```

The broadcast stays live: YouTube shows the holding screen for about a minute while the delay fills again.

### A machine set up before the plugin had its own save location

Its compose file came from GitHub when it was installed and isn't fetched again on update, so add the permissions to
it once. Open it:

```bash
sudo nano /opt/farmhand-stream/docker-compose.yml
```

and under `restart: unless-stopped` add (two spaces more than `stream:`, like the lines around it):

```yaml
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
```

Then update as above, and set **Where it saves** on Farmhand's Settings page (section 6). Its old folders (e.g.
`/mnt/stream:/media`) are only used until then: once the save location is connected, the `/etc/fstab` lines that
mounted them and their `volumes:` lines can go, or stay as they are.

## If it won't connect to the NAS

- **"The plugin's container isn't allowed to mount network shares"**: the compose file is missing the `cap_add` and
  `security_opt` lines (see just above), or, on Proxmox, the container's `features` and AppArmor lines (section 1).
- **"The NAS refused the connection"**: check the username and password, and that the user may use that share.
- **"Couldn't reach …"**: check the address, that the NAS is on, and that the plugin's machine is on the same network.
