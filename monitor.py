import requests
import time

# Configuration
WEBHOOK_URL = 'https://discord.com/api/webhooks/1475802958368084000/xPyX0o9cn5-YU_ac8I0TTQcQPe5N2xlgonoBBEW5GREBFRPRz3z3RMNAzVDfe3WNRYNc'
# Add any websites you want to monitor here
TARGET_SITES = ["https://www.google.com", "https://www.github.com", "https://httpstat.us/404"]

def send_discord_alert(site, status_code):
    data = {
        "content": f"🚨 **ALERT:** {site} is DOWN! Status Code: {status_code}",
        "username": "System Sentinel"
    }
    requests.post(WEBHOOK_URL, json=data)

def check_sites():
    print("Monitoring started... (Press Ctrl+C to stop)")
    while True:
        for site in TARGET_SITES:
            try:
                response = requests.get(site, timeout=5)
                if response.status_code != 200:
                    send_discord_alert(site, response.status_code)
                else:
                    print(f"[OK] {site} is healthy.")
            except Exception:
                send_discord_alert(site, "CONNECTION_FAILED")
        
        # Wait for 60 seconds before checking again
        time.sleep(60)

if __name__ == "__main__":
    check_sites()