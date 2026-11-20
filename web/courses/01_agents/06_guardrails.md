---
title: "When the Loop Goes Wrong"
subtitle: "An agent fails in ways a chatbot cannot"
minutes: 20
kernel: python
---

A chatbot that makes a mistake gives you a wrong sentence. An agent that makes a mistake
keeps going. In this lesson you break your agent on purpose, in the three ways agents usually
break, and make it survive each one.

## What you will do

- Crash the agent with a request for a tool that does not exist
- Turn failures into information the model can react to
- Watch the step limit stop an agent that would otherwise never finish

## Setup

The agent from the last lesson, with one change: `run_agent` now takes the model function as
an argument. That lets you swap in a fake model that misbehaves on cue, so you can study a
failure without waiting for a real model to produce it.

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


def run_agent(question, max_steps=6, model=call_model):
    """Answer a question, using tools as many times as the model asks for them."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        reply = model(messages)
        request = parse_tool_request(reply)

        if request is None:
            return reply

        result = TOOLS[request["tool"]](**request.get("args", {}))
        print(f"step {step}: {request.get('thought', '')}")
        print(f"   action: {request['tool']}({request.get('args', {})})")
        print(f"   result: {str(result)[:200]}")

        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": f"Tool result: {result}"})

    return "Stopped: the step limit was reached before the agent finished."
```

## 1. Failure one: a tool that does not exist

Models sometimes ask for a tool you never gave them, usually one with a plausible name. Here
is a fake model that always does exactly that:

```python
def confused_model(messages):
    """A stand-in model that asks for a tool nobody defined."""
    return '{"thought": "I will check the weather.", "tool": "get_weather", "args": {"city": "Paris"}}'


run_agent("What is the weather in Paris?", model=confused_model)
```

The cell ends in a `KeyError`. One bad request from the model took down your whole program.
The same thing happens when the model passes the wrong arguments or when a tool itself fails:

```python
def careless_model(messages):
    """A stand-in model that calls a real tool with an argument it does not accept."""
    return '{"thought": "Easy.", "tool": "calculate", "args": {"expression": "two plus two"}}'


run_agent("What is two plus two?", model=careless_model)
```

Another crash, this time a `ValueError` from inside the tool.

## 2. Turn failures into observations

The model is not your program. It will send things you did not expect, and the loop has to
cope. The useful trick is to treat an error as just another result: catch it, describe it in
words, and hand that back to the model. A model that reads "unknown tool" can correct itself
on the next step.

```python
def run_tool(request):
    """Run the requested tool. Problems come back as text, never as exceptions."""
    name = request.get("tool")
    args = request.get("args") or {}
    if name not in TOOLS:
        return f"Error: unknown tool {name!r}. Available tools: {', '.join(TOOLS)}."
    if not isinstance(args, dict):
        return "Error: args must be a JSON object."
    try:
        return str(TOOLS[name](**args))
    except Exception as error:
        return f"Error: {type(error).__name__}: {error}"


print(run_tool({"tool": "get_weather", "args": {"city": "Paris"}}))
print(run_tool({"tool": "calculate", "args": {"expression": "two plus two"}}))
print(run_tool({"tool": "calculate", "args": {"expression": "2 + 2"}}))
```

Now rewrite the loop to use it:

```python
def run_agent(question, max_steps=6, model=call_model):
    """Answer a question, using tools as many times as the model asks for them."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        reply = model(messages)
        request = parse_tool_request(reply)

        if request is None:
            return reply

        result = run_tool(request)
        print(f"step {step}: {request.get('thought', '')}")
        print(f"   action: {request.get('tool')}({request.get('args', {})})")
        print(f"   result: {result[:200]}")

        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": f"Tool result: {result}"})

    return "Stopped: the step limit was reached before the agent finished."
```

To see why this matters, here is a fake model that makes the same mistake but reads what
comes back:

```python
def recovering_model(messages):
    """A stand-in model that tries a missing tool, reads the error, and corrects itself."""
    last = messages[-1]["content"]
    if "unknown tool" in last:
        return '{"thought": "No such tool. calculate can do this.", "tool": "calculate", "args": {"expression": "6 * 7"}}'
    if last.startswith("Tool result:"):
        return "6 times 7 is 42."
    return '{"thought": "I will multiply.", "tool": "multiply", "args": {"a": 6, "b": 7}}'


run_agent("What is 6 times 7?", model=recovering_model)
```

The first step failed, the model read the error, picked a tool that exists, and finished.
The agent recovered without you. Real models do this routinely, but only if the loop gives
them the error to read.

## 3. Failure two: it never stops

Run the confused model again, now that errors no longer crash the loop:

```python
run_agent("What is the weather in Paris?", model=confused_model)
```

It asks for the same missing tool on every step and would do so forever. What stops it is
`max_steps`. That limit has been in your loop since you wrote it, and this is what it is for.

An agent without a step limit can spend your money and your time without end. Every real
agent has one, along with limits on time and cost.

```python
run_agent("What is the weather in Paris?", model=confused_model, max_steps=2)
```

## 4. Failure three: too much to read

A tool can return far more text than the model can hold. A search result or a file can be
thousands of lines, and every character is added to `messages` and resent on every later
step. You met this in the Memory lesson: the conversation has a size limit.

## Your turn

Complete `clip` so that a long result is cut to `limit` characters, with a note saying it was
cut. A result that is already short enough must come back unchanged.

```python
def clip(text, limit=2000):
    """Shorten text to at most `limit` characters, saying so when anything was removed."""
    return text  # FIXME: if text is longer than limit, return the first `limit` characters + "\n[truncated]"


len(clip("x" * 5000))
```

```check
task: "The agent survives bad tool requests, stops at its step limit, and `clip` shortens long results."
python: |
  assert run_tool({"tool": "nope"}).startswith("Error: unknown tool"), "run_tool should describe an unknown tool."
  assert run_tool({"tool": "calculate", "args": {"expression": "x"}}).startswith("Error: ValueError")
  assert run_tool({"tool": "calculate", "args": {"expression": "2 + 2"}}) == "4"
  import contextlib, io
  with contextlib.redirect_stdout(io.StringIO()):
      stopped = run_agent("x", model=confused_model, max_steps=2)
      recovered = run_agent("x", model=recovering_model)
  assert stopped.startswith("Stopped:"), "run_agent should stop at max_steps."
  assert "42" in recovered, "run_agent should let the model recover from a tool error."
  assert clip("short") == "short", "clip must not change short text."
  clipped = clip("x" * 5000, limit=100)
  assert clipped.startswith("x" * 100) and clipped.endswith("[truncated]"), "clip should keep `limit` characters and add [truncated]."
  assert len(clipped) < 130
hint: "`text[:limit]` is the first `limit` characters. Return it plus `\"\\n[truncated]\"` only when `len(text) > limit`."
success: "Your agent now handles bad requests, tool failures, runaway loops, and oversized results."
```

```solution
def clip(text, limit=2000):
    """Shorten text to at most `limit` characters, saying so when anything was removed."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


len(clip("x" * 5000))
```

To use it, change one line in `run_tool` to `return clip(str(TOOLS[name](**args)))`.

## What you have

| Problem | What handles it |
|---|---|
| The model asks for a tool that does not exist | `run_tool` returns an error the model can read |
| The model passes bad arguments, or the tool fails | The same: caught and described |
| The model never finishes | `max_steps` |
| A result is too long | `clip` |

Every one of these is a failure of the agent doing its job badly. The last lesson is about a
different kind of problem: an agent doing a job it was never given.

## Next

Continue to [Why a Sandbox](/learn/why-a-sandbox).
