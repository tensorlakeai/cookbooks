import datetime
from zoneinfo import ZoneInfo
from google.adk.agents import Agent
from tensorlake.applications import application, function, Image 

image = Image().run("pip install google-adk")


@function()
def get_weather(city: str) -> dict:
    """Retrieves the current weather report for a specified city.

    Args:
        city (str): The name of the city for which to retrieve the weather report.

    Returns:
        dict: status and result or error msg.
    """
    if city.lower() == "new york":
        return {
            "status": "success",
            "report": (
                "The weather in New York is sunny with a temperature of 25 degrees"
                " Celsius (77 degrees Fahrenheit)."
            ),
        }
    else:
        return {
            "status": "error",
            "error_message": f"Weather information for '{city}' is not available.",
        }

@function()
def get_current_time(city: str) -> dict:
    """Returns the current time in a specified city.

    Args:
        city (str): The name of the city for which to retrieve the current time.

    Returns:
        dict: status and result or error msg.
    """

    if city.lower() == "new york":
        tz_identifier = "America/New_York"
    else:
        return {
            "status": "error",
            "error_message": (
                f"Sorry, I don't have timezone information for {city}."
            ),
        }

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    report = (
        f'The current time in {city} is {now.strftime("%Y-%m-%d %H:%M:%S %Z%z")}'
    )
    return {"status": "success", "report": report}

@application()
@function(secrets=["GOOGLE_API_KEY"], image=image)
def google_adk_basic_agent(query: str, user_id: str, session_id: str) -> dict:
    root_agent = Agent(
        name="weather_time_agent",
        model="gemini-2.0-flash",
        description=(
            "Agent to answer questions about the time and weather in a city."
        ),
        instruction=(
            "You are a helpful agent who can answer user questions about the time and weather in a city."
        ),
        tools=[get_weather, get_current_time],
    )
    session_service = InMemorySessionService()
    session = session_service.create_session(app_name="weather_app", user_id=user_id, session_id=session_id)
    runner = Runner(agent=agent, app_name="weather_app", session_service=session_service)
    
    # Agent Interaction
    def call_agent(query):
        content = types.Content(role='user', parts=[types.Part(text=query)])
        events = runner.run(user_id=USER_ID, session_id=SESSION_ID, new_message=content)
    
        for event in events:
            print(f"\nDEBUG EVENT: {event}\n")
            if event.is_final_response() and event.content:
                final_answer = event.content.parts[0].text.strip()
                print("\n🟢 FINAL ANSWER\n", final_answer, "\n")
    
    resp = call_agent(query)
    return resp

