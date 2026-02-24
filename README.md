Sentinel: Lightweight System Observability Tool

A Python-based automated monitoring solution designed to track the uptime of web services and provide real-time incident alerting via Discord Webhooks.

Key Features
- Automated Health Checks:Periodically pings target URLs to verify service availability.
- Real-time Alerting:Instant downtime notifications delivered to Discord using Webhooks.
- Persistent Logging:Maintains a local `uptime.log` for audit trails and system analysis.
- Secure Configuration:Implemented industry-standard environment variable management (.env) to protect sensitive API credentials.

Tech Stack
- Language: Python 3.x
- Libraries:Requests (HTTP Library), Python-Dotenv (Security), Logging (Standard Library)
- Integration:Discord API (Webhooks)

Project Structure
- `monitor.py`: The core monitoring logic.
- `.env.example`: Template for required environment variables.
- `requirements.txt`: List of Python dependencies for easy setup.
- `uptime.log`: Generated file containing history of health checks.

 Installation & Setup

1. Clone the repository:
   ```bash
   git clone [https://github.com/JudeaTablate/sentinel-uptime-monitor.git](https://github.com/YOUR_USERNAME/sentinel-uptime-monitor.git)
   cd sentinel-uptime-monitor
2. Install dependencies:
   pip install -r requirements.txt
4. Configure Environment variables:
   Create a .env file in the root directory
   add your discord webhook url:
   DISCORD_WEBHOOK_URL=your_webhook_url_here
6. Run the Monitor:
   python monitor.py
<img width="510" height="71" alt="image" src="https://github.com/user-attachments/assets/9514260e-3fd7-46cf-9052-356e42818bc5" />
<img width="878" height="85" alt="image" src="https://github.com/user-attachments/assets/4933b97d-9d46-4c64-9369-e1b18aec1299" />
