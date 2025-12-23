"""
Incident memory and learning system.
Stores and retrieves past incident resolutions for self-improvement.
"""

import json
import os
from typing import List, Dict, Any, Tuple
from datetime import datetime

# DB File for persistence
DB_FILE = "incidents_db.json"
INCIDENTS: List[Dict[str, Any]] = []


def load_db():
    """Load incidents from JSON file"""
    global INCIDENTS
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                INCIDENTS = json.load(f)
            print(f"[MEMORY] Loaded {len(INCIDENTS)} incidents from disk.")
        except Exception as e:
            print(f"[MEMORY] Error loading DB: {e}")
            INCIDENTS = []
    else:
        print("[MEMORY] No existing database found. Starting fresh.")

def save_db():
    """Save incidents to JSON file"""
    try:
        with open(DB_FILE, "w") as f:
            json.dump(INCIDENTS, f, indent=2)
    except Exception as e:
        print(f"[MEMORY] Error saving DB: {e}")


# Load on import
load_db()


def add_incident_record(record: Dict[str, Any]) -> None:
    """
    Store a new incident record for future reference.
    
    In production, this should:
    1. Ingest the record into Indexify as a document
    2. Generate embeddings for vector search
    3. Index key fields (service, error type, timestamp)
    
    Args:
        record: Incident record containing service, error, root cause, actions, etc.
    """
    # Add timestamp if not present
    if "timestamp" not in record:
        record["timestamp"] = datetime.utcnow().isoformat()
    
    INCIDENTS.append(record)
    save_db()
    print(f"[MEMORY] Stored incident record for {record.get('service', 'unknown')}")


def find_similar_incidents(service: str, error_snippet: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Find most similar past incidents.
    
    Current implementation: Simple keyword matching
    Future implementation: Vector similarity search via Indexify
    """
    if not INCIDENTS:
        return []
        
    scored_incidents: List[Tuple[float, Dict[str, Any]]] = []
    
    # Simple semantic search simulation
    # In production, we would use embeddings here
    query_terms = set(error_snippet.lower().split())
    
    for incident in INCIDENTS:
        # Filter by service first (optional)
        if incident.get("service") != service:
            continue
            
        # Calculate overlap score
        target_text = (incident.get("error_snippet", "") + " " + incident.get("root_cause", "")).lower()
        target_terms = set(target_text.split())
        
        overlap = len(query_terms.intersection(target_terms))
        score = overlap / (len(query_terms) + 1)  # Simple Jaccard-ish score
        
        if score > 0.1:  # Threshold
            scored_incidents.append((score, incident))
    
    # Sort by score descending
    scored_incidents.sort(key=lambda x: x[0], reverse=True)
    
    return [incident for _, incident in scored_incidents[:top_k]]


def get_all_incidents() -> List[Dict[str, Any]]:
    """
    Retrieve all stored incidents.
    Useful for debugging and analytics.
    
    Returns:
        List of all incident records
    """
    return INCIDENTS.copy()


def clear_incidents() -> None:
    """
    Clear all stored incidents.
    Useful for testing and demo resets.
    """
    global INCIDENTS
    INCIDENTS = []
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except OSError:
            pass
    print("[MEMORY] Cleared all incident records")
