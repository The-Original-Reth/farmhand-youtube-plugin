# Installing the YouTube plugin on a Farmhand that doesn't have it

The plugin is a Docker container of its own, on Farmhand's machine or any machine on the network that can reach
Farmhand (port 4545) and the cameras.

The addresses here are examples (`192.168.10.240` the NAS, `192.168.10.90` Farmhand, `192.168.10.91` the plugin's
machine). Use your own.

## 1. Prepare the machine

Install **Docker** and **git** (Docker builds the plugin straight from GitHub).

If the stream and clips folders are on the NAS, make a `YouTube clips` folder there beside the stream folder, then add
these two lines to `/etc/fstab`. `/root/.nas-stream` is a credentials file you make, with `username=` and `password=`
lines, readable only by root.

```
//192.168.10.240/3d_Printing_Elves/Farmhand/stream /mnt/stream cifs credentials=/root/.nas-stream,vers=3.1.1,uid=0,gid=0,file_mode=0664,dir_mode=0775,iocharset=utf8,soft,_netdev,nofail 0 0
//192.168.10.240/3d_Printing_Elves/Farmhand/YouTube\040clips /mnt/youtube-clips cifs credentials=/root/.nas-stream,vers=3.1.1,uid=0,gid=0,file_mode=0664,dir_mode=0775,iocharset=utf8,soft,_netdev,nofail 0 0
```

`\040` stands for the space in "YouTube clips": write it just like that. Then mount them:

```bash
sudo mkdir -p /mnt/stream /mnt/youtube-clips && sudo mount -a
```

## 2. Get the compose file and edit it

```bash
sudo mkdir -p /opt/farmhand-stream && cd /opt/farmhand-stream
```

```bash
sudo curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/The-Original-Reth/farmhand-youtube-plugin/main/docker-compose.yml
```

```bash
sudo nano /opt/farmhand-stream/docker-compose.yml
```

- `./media:/media` becomes `/mnt/stream:/media`.
- `./clips:/clips` becomes `"/mnt/youtube-clips:/clips"`.
- Optional: uncomment `environment:` and set `STREAM_FOLDER_SHOWN_AS` and `STREAM_CLIPS_SHOWN_AS`, so the page shows
  the folders' network paths.
- Optional, for an NVIDIA card: uncomment the `devices:` and library lines (driver set-up: the
  [README](README.md#an-nvidia-card), "An NVIDIA card").

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

**YouTube** is now in Farmhand's menu, after Monitor. There: sign in to YouTube (**YouTube settings**) or paste a stream
key, choose the grid and cameras, and press **Go live**.

**Clips** work once the clips folder is mounted: type a length (1 second to 5 minutes) and press **Save clip**.

## Updating

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --build
```

The broadcast stays live: YouTube shows the holding screen for about a minute while the delay fills again.
