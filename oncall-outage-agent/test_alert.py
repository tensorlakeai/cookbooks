"""
Test script to send sample alerts to the outage agent webhook.
Use this to test the agent locally.
"""

import requests
import json
from datetime import datetime

# Webhook URL
WEBHOOK_URL = "http://localhost:8000/alert"


def send_alert(alert_data):
    """Send an alert to the webhook"""
    print("\n" + "="*80)
    print("📤 SENDING TEST ALERT")
    print("="*80)
    print(json.dumps(alert_data, indent=2))
    print("="*80 + "\n")
    
    try:
        response = requests.post(WEBHOOK_URL, json=alert_data, timeout=120)
        response.raise_for_status()
        
        print("\n" + "="*80)
        print("✅ RESPONSE RECEIVED")
        print("="*80)
        print(json.dumps(response.json(), indent=2))
        print("="*80 + "\n")
        
        return response.json()
    
    except requests.exceptions.ConnectionError:
        print("\n❌ ERROR: Could not connect to webhook.")
        print("Make sure the FastAPI server is running:")
        print("  python main.py")
        print("  OR")
        print("  uvicorn main:app --reload --port 8000\n")
        return None
    
    except requests.exceptions.Timeout:
        print("\n⏱️  REQUEST TIMED OUT")
        print("The agent is still processing (this can take 30-60 seconds)")
        print("Check the server logs for progress\n")
        return None
    
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}\n")
        return None


def test_kafka_timeout():
    """Test case: Kafka timeout causing payment failures"""
    alert = {
        "service": "payments-api",
        "severity": "critical",
        "summary": "High error rate on /charge endpoint - KafkaTimeoutException",
        "timestamp": datetime.utcnow().isoformat(),
        "labels": {
            "env": "production",
            "region": "us-central1",
            "team": "payments",
            "error_type": "KafkaTimeoutException",
        },
    }
    return send_alert(alert)


def test_memory_leak():
    """Test case: Memory leak causing OOM errors"""
    alert = {
        "service": "user-service",
        "severity": "critical",
        "summary": "Out of memory errors - container restarting every 10 minutes",
        "timestamp": datetime.utcnow().isoformat(),
        "labels": {
            "env": "production",
            "region": "eu-west-1",
            "team": "identity",
            "error_type": "OutOfMemoryError",
        },
    }
    return send_alert(alert)


def test_database_connection():
    """Test case: Database connection pool exhausted"""
    alert = {
        "service": "inventory-api",
        "severity": "warning",
        "summary": "Database connection pool exhausted - requests timing out",
        "timestamp": datetime.utcnow().isoformat(),
        "labels": {
            "env": "production",
            "region": "ap-southeast-1",
            "team": "logistics",
            "error_type": "ConnectionPoolExhausted",
        },
    }
    return send_alert(alert)


def test_deployment_issue():
    """Test case: Bad deployment causing 500 errors"""
    alert = {
        "service": "checkout-service",
        "severity": "critical",
        "summary": "Spike in 500 errors after v2.4.0 deployment",
        "timestamp": datetime.utcnow().isoformat(),
        "labels": {
            "env": "production",
            "region": "us-east-1",
            "team": "commerce",
            "deployed_version": "v2.4.0",
            "previous_version": "v2.3.5",
        },
    }
    return send_alert(alert)


if __name__ == "__main__":
    import sys
    
    print("\n🧪 OUTAGE AGENT TEST SUITE")
    print("="*80)
    
    # Check if server is running
    try:
        health = requests.get("http://localhost:8000/health", timeout=5)
        print("✅ Server is running")
        print(f"Health: {health.json()}\n")
    except:
        print("❌ Server is not running!")
        print("\nStart the server first:")
        print("  python main.py")
        print("  OR")
        print("  uvicorn main:app --reload --port 8000\n")
        sys.exit(1)
    
    # Run test cases
    test_cases = [
        ("Kafka Timeout", test_kafka_timeout),
        ("Memory Leak", test_memory_leak),
        ("Database Connection", test_database_connection),
        ("Bad Deployment", test_deployment_issue),
    ]
    
    print("\nAvailable test cases:")
    for idx, (name, _) in enumerate(test_cases, 1):
        print(f"  {idx}. {name}")
    print(f"  {len(test_cases) + 1}. Run all tests")
    
    choice = input("\nSelect test case (1-5): ").strip()
    
    if choice == str(len(test_cases) + 1):
        # Run all tests
        for name, test_func in test_cases:
            print(f"\n\n{'#'*80}")
            print(f"# TEST: {name}")
            print(f"{'#'*80}\n")
            test_func()
            input("\nPress Enter to continue to next test...")
    elif choice.isdigit() and 1 <= int(choice) <= len(test_cases):
        # Run selected test
        name, test_func = test_cases[int(choice) - 1]
        print(f"\nRunning test: {name}\n")
        test_func()
    else:
        print("Invalid choice")
