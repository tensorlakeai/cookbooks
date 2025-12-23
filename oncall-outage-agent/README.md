# 🚨 On-Call Outage Agent

**An intelligent incident response agent that fixes production outages before you wake up.**

Powered by [Tensorlake](https://tensorlake.ai) (Indexify Runtime), this agent automatically:
- 🔍 Diagnoses production incidents using logs, metrics, and past incidents
- 🧠 Reasons about root causes with Groq LLM
- 🔧 Executes safe automated fixes (rollbacks, restarts)
- 📚 Learns from every incident for faster future responses
- 💬 Posts detailed postmortems to Slack

## 🎯 Demo Concept

When a production alert fires (e.g., from Prometheus, Grafana, or PagerDuty):

1. **Alert arrives** → Agent receives webhook
2. **Context gathering** → Fetches logs, metrics, and similar past incidents
3. **Web research** → Uses Exa to search for known issues
4. **AI reasoning** → Groq LLM analyzes and decides on action
5. **Automated fix** → Executes rollback/restart if confident
6. **Verification** → Monitors metrics post-fix
7. **Documentation** → Writes postmortem and stores in memory
8. **Notification** → Posts to Slack with full incident report

**The self-improving bit:** Each incident enriches the knowledge base, making future responses faster and smarter.

## 🏗️ Architecture

```
┌─────────────────┐
│ Alert Source    │
│ (Prometheus,    │
│  Grafana, etc)  │
└────────┬────────┘
         │ HTTP Webhook
         ▼
┌─────────────────────────────────────┐
│ FastAPI Endpoint (/alert)          │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Tensorlake/Indexify Graph           │
│   handle_outage()                   │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ LangChain Agent + Groq LLM          │
│                                     │
│ Tools:                              │
│  • get_logs()                       │
│  • get_metrics()                    │
│  • search_web() [Exa]               │
│  • rollback_deploy()                │
│  • restart_service()                │
│  • capture_dashboard() [Browserbase]│
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Incident Memory (in-memory / Indexify)│
│  • Store incident resolutions       │
│  • Retrieve similar past incidents  │
└─────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- [Indexify](https://docs.tensorlake.ai) server (optional for MVP, required for production)
- API Keys:
  - [Groq](https://console.groq.com) (required)
  - [Exa](https://exa.ai) (optional, for web search)
  - [Browserbase](https://browserbase.com) (optional, for dashboard screenshots)

### Installation

1. **Clone and navigate**
   ```bash
   cd /Users/mac/tensorlake\ project/outage-agent
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   ```

4. **Set environment variables**
   ```bash
   export GROQ_API_KEY="your_groq_api_key"
   export EXA_API_KEY="your_exa_api_key"  # Optional
   export BROWSERBASE_API_KEY="your_browserbase_key"  # Optional
   ```

### Running the Agent

#### Option 1: Local Test (No Server)

Test the agent directly with a simulated alert:

```bash
python -m outage_agent.indexify_app
```

This runs a standalone test with a fake alert for `payments-api`.

#### Option 2: Webhook Server

Start the FastAPI server to receive real webhooks:

```bash
python main.py
# OR
uvicorn main:app --reload --port 8000
```

The server will be available at:
- Webhook: `http://localhost:8000/alert`
- Health: `http://localhost:8000/health`
- Incidents: `http://localhost:8000/incidents`

#### Option 3: Send Test Alerts

In another terminal, run the test script:

```bash
python test_alert.py
```

This provides an interactive menu to send realistic test alerts:
1. Kafka Timeout
2. Memory Leak
3. Database Connection Issues
4. Bad Deployment

### Example Alert Payload

Send alerts via POST request:

```bash
curl -X POST http://localhost:8000/alert \
  -H "Content-Type: application/json" \
  -d '{
    "service": "payments-api",
    "severity": "critical",
    "summary": "High error rate on /charge - KafkaTimeoutException",
    "timestamp": "2025-12-16T22:45:00Z",
    "labels": {
      "env": "production",
      "region": "us-central1",
      "team": "payments"
    }
  }'
```

## 🧪 Testing

### Unit Tests

Test individual tools:

```bash
python -c "from outage_agent.tools import get_logs; print(get_logs('test-service'))"
python -c "from outage_agent.tools import get_metrics; import json; print(json.dumps(get_metrics('test-service'), indent=2))"
```

### Integration Test

1. Start the webhook server:
   ```bash
   python main.py
   ```

2. Send a test alert:
   ```bash
   python test_alert.py
   ```

3. Observe the agent:
   - Gathering logs and metrics
   - Searching the web for solutions
   - Reasoning about root cause
   - Deciding on actions
   - Executing fixes (simulated)
   - Posting to Slack (console output)

### Expected Output

You should see:
- 🚨 Alert received
- 🔍 Tool calls (logs, metrics, web search)
- 🧠 LLM reasoning steps
- ✅ Actions taken (rollback/restart/escalate)
- 💬 Slack notification with postmortem
- 📝 Incident stored in memory

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ Yes | Groq API key for LLM inference |
| `EXA_API_KEY` | ❌ No | Exa API key for web search |
| `BROWSERBASE_API_KEY` | ❌ No | Browserbase key for screenshots |
| `INDEXIFY_URL` | ❌ No | Indexify server URL (default: localhost:8900) |

### Tool Stubs

The following tools are **stubbed** for demo purposes:

- `get_logs()` - Returns fake log data
- `get_metrics()` - Returns mock metrics
- `rollback_deploy()` - Simulates rollback
- `restart_service()` - Simulates restart
- `send_slack_message()` - Prints to console

**For production:** Integrate these with your actual systems:
- Logs: Datadog, Loki, CloudWatch, Elasticsearch
- Metrics: Prometheus, Grafana, Datadog
- Deployment: Argo CD, GitHub Actions, Jenkins, Spinnaker
- Orchestration: Kubernetes API, Docker, AWS ECS
- Notifications: Slack API, PagerDuty, email

## 📚 Production Deployment

### With Indexify

1. **Start Indexify server**
   ```bash
   indexify-cli server-dev-mode
   ```

2. **Uncomment decorators in `indexify_app.py`**
   ```python
   from tensorlake.applications import application, function
   
   @application()
   @function()
   def handle_outage(alert: Dict[str, Any]) -> Dict[str, Any]:
       # ...
   ```

3. **Deploy the graph**
   ```python
   from indexify import RemoteGraph
   
   graph = RemoteGraph.deploy(handle_outage)
   ```

4. **Configure webhook to hit Indexify endpoint**

### Webhook Configuration

#### Prometheus Alertmanager

```yaml
receivers:
  - name: 'outage-agent'
    webhook_configs:
      - url: 'http://localhost:8000/alert'
        send_resolved: true
```

#### Grafana

Add a webhook notification channel:
- URL: `http://localhost:8000/alert`
- HTTP Method: POST

#### PagerDuty

Configure a webhook extension:
- Endpoint: `http://localhost:8000/alert`

## 🎬 Creating a Viral Demo

Record a demo showing:

1. **Alert fires** - Show Grafana/Prometheus dashboard with red metrics
2. **Agent activates** - Terminal shows agent receiving webhook
3. **Investigation** - Agent fetches logs, metrics, searches web
4. **Decision** - LLM decides to rollback deployment
5. **Execution** - Rollback command executed
6. **Verification** - Metrics return to green
7. **Postmortem** - Slack message with full incident report
8. **Learning** - Second similar incident resolves in seconds

**Overlay text:**
> "This wasn't a human.  
> It was an agent running on Tensorlake."

## 🛠️ Extending the Agent

### Add a New Tool

1. Create the function in `outage_agent/tools.py`:
   ```python
   def scale_service(service: str, replicas: int) -> str:
       # Implementation
       return f"Scaled {service} to {replicas} replicas"
   ```

2. Wrap it for LangChain in `outage_agent/agent.py`:
   ```python
   @tool
   def lc_scale_service(service: str, replicas: int) -> str:
       """Scale a service to handle increased load."""
       return scale_service(service, replicas)
   
   TOOLS.append(lc_scale_service)
   ```

3. The agent will automatically use it when appropriate!

### Upgrade to Indexify Memory

Replace in-memory storage with Indexify:

1. **Ingest incidents as documents**
   ```python
   from indexify import IndexifyClient
   
   client = IndexifyClient()
   client.ingest_documents(
       namespace="incidents",
       documents=[incident_record]
   )
   ```

2. **Query with vector search**
   ```python
   results = client.search(
       namespace="incidents",
       query=error_snippet,
       top_k=3
   )
   ```

## 📖 Project Structure

```
outage-agent/
├── outage_agent/
│   ├── __init__.py          # Package init
│   ├── tools.py             # Infrastructure tools + Exa + Browserbase
│   ├── memory.py            # Incident storage & retrieval
│   ├── agent.py             # LangChain agent + Groq LLM
│   └── indexify_app.py      # Tensorlake application graph
├── main.py                  # FastAPI webhook server
├── test_alert.py            # Test script with sample alerts
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
└── README.md               # This file
```

## 🤝 Contributing

Ideas for improvements:
- Integrate real observability systems
- Add more sophisticated incident similarity matching
- Implement canary deployment tool
- Add metric verification after fixes
- Support multi-service incidents
- Generate Grafana dashboard screenshots
- Email postmortem reports

## 📝 License

MIT License - feel free to use for demos, production, or learning!

## 🙏 Acknowledgments

Built with:
- [Tensorlake](https://tensorlake.ai) - Workflow orchestration
- [Groq](https://groq.com) - Fast LLM inference
- [LangChain](https://langchain.com) - Agent framework
- [Exa](https://exa.ai) - Web search
- [Browserbase](https://browserbase.com) - Browser automation
- [FastAPI](https://fastapi.tiangolo.com) - HTTP framework

---

**Ready to fix incidents before you wake up?** 🚀
