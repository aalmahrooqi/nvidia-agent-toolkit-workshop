---
title: "The Agent Loop"
subtitle: "Think, act, look at the result, repeat"
minutes: 20
kernel: python
---

So far you have run each tool call by hand: call the model, read its request, run the tool,
call the model again. In this lesson you put those steps in a loop. That loop is what the
word "agent" means.

```agent-map
step: loop
```

## What you will do

- Ask a question that needs more than one tool
- Write `run_agent`, a loop that keeps going until the model has an answer
- Have the model explain each step, which gives you the pattern known as ReAct

## Setup

The model call, three tools, and the parser, as you left them:

```python
import json
import os
import re
from datetime import datetime

import httpx

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"


def call_model(messages):
    """Send messages to the model and return its reply as text."""
    response = httpx.post(
        API_URL,
        headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"},
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 4096,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def get_time():
    """The current local date and time."""
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def calculate(expression):
    """Evaluate arithmetic such as '17 * (4 + 9)'."""
    if not re.fullmatch(r"[0-9+\-*/(). ]+", expression):
        raise ValueError("only numbers and + - * / ( ) are allowed")
    return str(eval(expression, {"__builtins__": {}}))


def web_search(query):
    """Search the web and return the top results as text."""
    key = os.environ.get("TAVILY_API_KEY")
    response = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"} if key else {},
        json={"query": query, "max_results": 3},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{item['title']}\n{item['url']}\n{item['content'][:400]}" for item in results
    )


TOOLS = {"get_time": get_time, "calculate": calculate, "web_search": web_search}


def parse_tool_request(reply):
    """Return the tool request in a reply as a dict, or None if it is a plain answer."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return None
    try:
        request = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return request if isinstance(request, dict) and "tool" in request else None
```

## 1. A question that needs two steps

```text
What year is it, and what is that year to the power of 3?
```

Answering takes two tools in order: `get_time` to learn the year, then `calculate` with the
number it found. The second request depends on the result of the first, so the model cannot
ask for both at once. Your by-hand code handled exactly one tool call and then stopped.

## 2. Ask the model to think out loud

Add one field to the request format: a `thought`, where the model says why it wants the tool.
This costs nothing and makes the agent's behavior readable.

```python
SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"thought": "<why you need this tool>", "tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}
- calculate: evaluate arithmetic. args: {"expression": "2 * (3 + 4)"}
- web_search: search the web for current information. args: {"query": "..."}

Use one tool at a time. After each tool result, decide whether you need another tool.
When you have everything you need, reply with the final answer in plain text.
When your answer uses a search result, include the URL it came from."""
```

## 3. Write the loop

Read this carefully. It is short, and it is the centre of the whole course.

```python
def run_agent(question, max_steps=6):
    """Answer a question, using tools as many times as the model asks for them."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        reply = call_model(messages)
        request = parse_tool_request(reply)

        if request is None:
            return reply  # a plain answer: the agent is finished

        result = TOOLS[request["tool"]](**request.get("args", {}))
        print(f"step {step}: {request.get('thought', '')}")
        print(f"   action: {request['tool']}({request.get('args', {})})")
        print(f"   result: {str(result)[:200]}")

        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": f"Tool result: {result}"})

    return "Stopped: the step limit was reached before the agent finished."
```

Each pass through the loop does what you did by hand:

1. **Think.** Call the model with everything so far.
2. **Decide.** If the reply is a plain answer, return it. Otherwise it is a tool request.
3. **Act.** Run the tool.
4. **Observe.** Add the result to the conversation, and go round again.

The model chooses how many times to go round. Your code only decides when it has to stop.

## 4. Run it

```python
answer = run_agent("What year is it, and what is that year multiplied by 3?")
print()
print(answer)
```

Read the trace. The model got the date, read the year out of the result, and only then asked
for the calculation, using a number it did not know when the question was asked. Nobody wrote
that plan. It came from the loop giving the model a chance to look at each result before
deciding what to do next.

Try one that mixes search and arithmetic:

```python
answer = run_agent(
    "How tall is Mount Everest in metres according to a web search, "
    "and what is that height divided by 3?"
)
print()
print(answer)
```

## What you just built

This pattern has a name: **ReAct**, for *Reason* and *Act*. The model reasons about what it
needs (the `thought`), acts by requesting a tool, observes the result, and repeats. Most
agents you will meet, including coding assistants and the OpenClaw agent in the next course,
are this loop with more tools and more engineering around it.

| Piece | In your code |
|---|---|
| The model | `call_model` |
| Tools | The `TOOLS` dictionary |
| Memory | The `messages` list |
| The loop | The `for` in `run_agent` |

An agent is those four things. There is no fifth.

```check
task: "`run_agent` loops over tool calls and returns a final answer."
python: |
  import inspect
  assert callable(run_agent), "Run the cell that defines run_agent."
  assert "max_steps" in inspect.signature(run_agent).parameters
  assert "thought" in SYSTEM_PROMPT, "SYSTEM_PROMPT should ask for a thought."
  assert isinstance(answer, str) and answer.strip(), "Run the cells in section 4."
  assert not answer.startswith("Stopped:"), "The agent hit its step limit. Run the cell again."
hint: "Run every cell from the top. If a search step fails, add a Tavily key in the Web Search lesson; keys are shared across lessons."
success: "You have a working agent: a model, tools, memory, and a loop."
```

## It is fragile

Your agent works when everything goes well. Look at this line again:

```text
result = TOOLS[request["tool"]](**request.get("args", {}))
```

It trusts the model to name a real tool, to pass the right arguments, and it trusts the tool
not to fail. None of those is guaranteed.

## Next

Continue to [When the Loop Goes Wrong](/learn/guardrails).
