import sys
from io import StringIO
from tensorlake.applications import application, function, Image

# Image for the code execution container - has data science libraries
code_exec_image = (
    Image(name="python:3.11-slim")
    .run("pip install numpy pandas matplotlib")
)

# Image for the agent container - has the OpenAI Agent SDK
agent_image = (
    Image(name="python:3.11-slim")
    .run("pip install openai-agents")
)

@function(image=code_exec_image, cpu=2, memory=4)
def execute_code(code: str) -> str:
    """Execute Python code in a secure sandbox and return the output."""
    stdout_capture = StringIO()
    old_stdout = sys.stdout
    
    try:
        sys.stdout = stdout_capture
        exec_globals = {"__builtins__": __builtins__}
        exec(code, exec_globals)
        sys.stdout = old_stdout
        return stdout_capture.getvalue()
    except Exception as e:
        sys.stdout = old_stdout
        return f"Error: {e}\nOutput: {stdout_capture.getvalue()}"

@application()
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def code_interpreter_agent(user_request: str) -> str:
    """Run the agentic loop and return the final answer."""

    from agents import Agent, Runner, function_tool
    
    @function_tool
    def execute_python(code: str) -> str:
        """Execute Python code in a secure sandbox. Use this for calculations or data analysis."""
        return execute_code(code)
    
    agent = Agent(
        name="Code interpreter",
        model="gpt-4o",
        instructions="You are a helpful assistant that can execute Python code to solve problems.",
        tools=[execute_python],
    )
    
    result = Runner.run_sync(agent, user_request)
    return result.final_output
