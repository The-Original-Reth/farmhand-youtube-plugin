# Installing the YouTube plugin on a Farmhand that doesn't have it

The plugin runs in its own Docker container. It can go on Farmhand's machine or any other machine on the farm's network
that can reach Farmhand (port 4545) and the cameras.

The addresses below are examples: `192.168.10.240` is the NAS, `192.168.10.90` is Farmhand and `192.168.10.91` is the
plugin's machine. Use your own.

## 1. Prepare the machine

The machine needs **Docker** and **git**, because Docker builds the plugin straight from GitHub.

If the stream folder and the clips folder are on the NAS, mount both shares first. Add these two lines to `/etc/fstab`,
with a credentials file you create (`username=` and `password=` lines, readable only by root):

```
//192.168.10.240/3d_Printing_Elves/Farmhand/stream /mnt/stream cifs credentials=/root/.nas-stream,vers=3.1.1,uid=0,gid=0,file_mode=0664,dir_mode=0775,iocharset=utf8,soft,_netdev,nofail 0 0
//192.168.10.240/3d_Printing_Elves/Farmhand/YouTube\040clips /mnt/youtube-clips cifs credentials=/root/.nas-stream,vers=3.1.1,uid=0,gid=0,file_mode=0664,dir_mode=0775,iocharset=utf8,soft,_netdev,nofail 0 0
```

`\040` is the space in "YouTube clips": write it like that. Make the `YouTube clips` folder on the NAS first, beside the
stream folder. Then mount them:

```bash
sudo mkdir -p /mnt/stream /mnt/youtube-clips && sudo mount -a
```

## 2. Get the compose file

```bash
sudo mkdir -p /opt/farmhand-stream && cd /opt/farmhand-stream
```

```bash
sudo curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/The-Original-Reth/farmhand-youtube-plugin/main/docker-compose.yml
```

Edit it:

```bash
sudo nano /opt/farmhand-stream/docker-compose.yml
```

- Change `./media:/media` to `/mnt/stream:/media`.
- Change `./clips:/clips` to `"/mnt/youtube-clips:/clips"`.
- Optional: uncomment `environment:` and set `STREAM_FOLDER_SHOWN_AS` and `STREAM_CLIPS_SHOWN_AS`, so the page shows
  the folders' network paths.
- Optional: for an NVIDIA card, uncomment the `devices:` and library lines. The [README](README.md#an-nvidia-card),
  under "An NVIDIA card", covers the driver setup.

## 3. Start it

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --build
```

It now answers at `http://<that machine>:8080`, but doesn't know Farmhand yet.

## 4. Add it in Farmhand and get its token

1. In Farmhand, open **Plugins** (in the left-hand menu, under Settings).
2. Under **Add a plugin**, type the plugin's address, e.g. `http://192.168.10.91:8080`, and press **Look at it**.
3. Farmhand shows what the plugin asks to read, as tick boxes:
   - `cameras:read`: your printers' camera addresses
   - `prints:read`: what each printer is printing
   - `stream:migrate`: only matters if this Farmhand once had the built-in Stream page; untick it to start fresh
4. Leave **Only accept its token from its own address** ticked, so the token is useless from any other machine.
5. Press **Add YouTube Stream**. A window titled **"Give YouTube Stream its token"** opens, showing:
   ```
   FARMHAND_URL=http://192.168.10.90:4545/plugin-api/v1
   FARMHAND_TOKEN=Abc123…   (a long random string)
   ```
6. Press **Copy**. **This is the only time Farmhand shows the token**: it keeps only a scrambled copy. The window doesn't
   close if you click outside it; only **Done** closes it, so leave it open until the next step is done.

About `FARMHAND_URL`: it's the address your browser used for Farmhand. If the plugin runs on another machine, make sure
that machine can reach that address, e.g. Farmhand's LAN IP. If the plugin runs in Docker on Farmhand's own machine,
`http://api:4545/plugin-api/v1` also works.

## 5. Give the plugin its token

On the plugin's machine, paste the two lines into `stream.env`, next to `docker-compose.yml`:

```bash
sudo nano /opt/farmhand-stream/stream.env
```

Restart the plugin so it reads them:

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --force-recreate
```

Then press **Done** in Farmhand.

**Lost the token, or closed the window too early?** On the Plugins page, find YouTube Stream in the list and press
**New token…**, then **Make a new token**. The old token stops working at once, and the same window shows the new one.
Put it in `stream.env` and restart the plugin as above.

## 6. Set it up

**YouTube** now appears in Farmhand's menu after Monitor. On that page:

1. Sign in to YouTube (**YouTube settings**), or paste a stream key.
2. Choose the grid and cameras.
3. Press **Go live**.

**Clips** work as soon as the clips folder is mounted. Type a length (1 second to 5 minutes) and press **Save clip**.

## Updating later

```bash
cd /opt/farmhand-stream && sudo docker compose up -d --build
```

The broadcast stays live. YouTube shows the holding screen for about a minute while the delay refills.
