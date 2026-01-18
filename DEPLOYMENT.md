# TrackUI Deployment Guide for Ubuntu Homelab

## Prerequisites

Make sure your Ubuntu server has Docker and Docker Compose installed:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh

# Add your user to docker group (logout/login required)
sudo usermod -aG docker $USER

# Install Docker Compose (if not included)
sudo apt install docker-compose-plugin -y

# Verify installation
docker --version
docker compose version
```

---

## Quick Deployment

### 1. Transfer Files to Server

**Option A: Using Git (Recommended)**
```bash
# On your Ubuntu server
cd ~
git clone <your-repo-url> trackui
cd trackui
```

**Option B: Using SCP from Windows**
```powershell
# From Windows PowerShell
scp -r "C:\Users\orrei\Desktop\vibecoding\TrackUI Redo" user@your-ubuntu-ip:~/trackui
```

**Option C: Using SFTP/FileZilla**
- Connect to your Ubuntu server
- Upload the entire project folder to `~/trackui`

---

### 2. Build and Run

```bash
cd ~/trackui

# Build the Docker image
docker compose build

# Start the container
docker compose up -d

# Check logs
docker compose logs -f
```

---

### 3. Access the Application

- **Local**: `http://localhost:5000`
- **Network**: `http://<your-server-ip>:5000`

Find your server's IP:
```bash
hostname -I | awk '{print $1}'
```

---

## Configuration

### Change Timezone

Edit `docker-compose.yml` and update the `TZ` environment variable:
```yaml
environment:
  - TZ=Europe/London  # Your timezone
```

### Change Port

Edit `docker-compose.yml`:
```yaml
ports:
  - "8080:5000"  # Access via port 8080 instead
```

### Persistent Data

All data is stored in the `./data` folder which is mounted as a volume:
- `data/trackui.db` - SQLite database
- `data/downloads/` - Downloaded media
- `data/cookies/` - Cookie files for authentication
- `data/avatars/` - Profile pictures

---

## Management Commands

```bash
# View logs
docker compose logs -f trackui

# Restart container
docker compose restart

# Stop container
docker compose down

# Rebuild after code changes
docker compose build --no-cache
docker compose up -d

# View container status
docker compose ps

# Enter container shell
docker exec -it trackui /bin/bash
```

---

## Reverse Proxy (Optional)

### Using Nginx

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/trackui
```

Add configuration:
```nginx
server {
    listen 80;
    server_name trackui.local;  # Or your domain

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if needed)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/trackui /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Using Traefik (with labels)

Add to `docker-compose.yml`:
```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.trackui.rule=Host(`trackui.yourdomain.com`)"
  - "traefik.http.services.trackui.loadbalancer.server.port=5000"
```

---

## Troubleshooting

### Container won't start
```bash
# Check logs for errors
docker compose logs trackui

# Check if port is in use
sudo lsof -i :5000
```

### Database errors
```bash
# Reset database (backup first!)
docker compose down
rm data/trackui.db
docker compose up -d
```

### Permission issues
```bash
# Fix ownership of data folder
sudo chown -R $USER:$USER data/
chmod -R 755 data/
```

---

## Updating from GitHub

If you cloned from Git, updating is just **3 commands**:

```bash
cd ~/trackui
git pull origin main
docker compose build
docker compose up -d
```

### One-Command Update Script

Create `~/update-trackui.sh`:
```bash
#!/bin/bash
cd ~/trackui
echo "📥 Pulling latest changes..."
git pull origin main
echo "🔨 Rebuilding container..."
docker compose build
echo "🚀 Restarting..."
docker compose down && docker compose up -d
echo "✅ Update complete!"
```

Make executable and use:
```bash
chmod +x ~/update-trackui.sh
~/update-trackui.sh
```

### What's Preserved During Updates?

| ✅ Preserved | 🔄 Replaced |
|--------------|-------------|
| Database, Downloads, Cookies, Avatars | App code, Docker image |

Your data is safe — it's in the `./data` volume mount!

---

## Auto-start on Boot

Docker containers with `restart: unless-stopped` will automatically start when the server reboots.

Ensure Docker service is enabled:
```bash
sudo systemctl enable docker
```

---

## Backup

```bash
# Backup data folder
tar -czvf trackui-backup-$(date +%Y%m%d).tar.gz data/

# Or just the database
cp data/trackui.db data/trackui-backup.db
```
