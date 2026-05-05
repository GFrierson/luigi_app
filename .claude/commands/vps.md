You have SSH access to Luigi's cloud VPS instance. Use it to inspect logs, check service status, deploy updates, or run any other remote operation the user requests.

## Connection details
- **SSH alias:** `vps` (configured in ~/.ssh/config)
- **Host:** 100.80.30.12
- **User:** luigi
- **Identity file:** ~/.ssh/luigi_vps
- **App location on server:** /opt/luigi_app

## Common operations

**View recent logs (last 100 lines):**
```
ssh vps "journalctl -u luigi -n 100 --no-pager"
```

**Follow logs live:**
```
ssh vps "journalctl -u luigi -f"
```

**Check service status:**
```
ssh vps "sudo systemctl status luigi"
```

**Restart the service:**
```
ssh vps "sudo systemctl restart luigi"
```

**Deploy latest code:**
```
ssh vps "cd ~/luigi_app && git pull && sudo systemctl restart luigi"
```

**Run an arbitrary remote command:**
```
ssh vps "<command>"
```

## Instructions

The user's request is: $ARGUMENTS

If no argument was given, default to showing the last 50 log lines and the current service status.

Run the appropriate SSH command(s) via Bash, then summarize the output for the user. For logs, highlight any ERROR or WARNING lines. For deployments, confirm the service restarted cleanly.
