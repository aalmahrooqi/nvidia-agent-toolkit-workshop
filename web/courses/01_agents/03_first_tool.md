---
title: "A First Tool"
subtitle: "The model asks, your code acts"
minutes: 20
kernel: python
---

A model can only produce text. It cannot look at a clock, open a file, or do anything else in
the world. In this lesson you give it a way to ask your program to do those things. That one
idea, a **tool**, is what turns a chatbot into the start of an agent.

```agent-map
step: tools
```

## What you will do

- Ask the model something it cannot possibly know
- Teach it a simple way to request help
- Run that request in your own code and hand the result back

## Setup

`call_model` from before, unchanged:

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
```

## 1. Ask something it cannot know

```python
print(call_model([{"role": "user", "content": "What is the exact time right now?"}]))
```

It either says it cannot know or it guesses. Both are the right behavior for a model, because
the time is not in its training data and not in your message. No amount of memory fixes this.
The model needs information from outside itself.

Your program, on the other hand, can find out the time with one line:

```python
def get_time():
    """The current local date and time."""
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


get_time()
```

The job is to connect the two: let the model say "I need the time", and have your code call
`get_time` for it.

## 2. Agree on how the model asks

The model can only write text, so a request for help has to be text too. You pick a format
that is easy for code to recognize, and describe it in the system prompt. JSON is the usual
choice.

```python
SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}

After you receive a tool result, answer the user in plain text."""
```

That prompt is the whole "tool calling" mechanism. There is nothing more to it than an
agreement about what the model should write when it wants something.

Ask again, with the prompt in place:

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "What is the exact time right now?"},
]
reply = call_model(messages)
print(reply)
```

Instead of guessing, the model wrote a request. Nothing has run yet. The model did not call
`get_time`; it cannot. It produced text that says it would like you to.

## 3. Read the request

Your code needs to tell a tool request apart from a normal answer. This function looks for a
JSON object in the reply and returns it if it names a tool.

```python
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


request = parse_tool_request(reply)
request
```

## 4. Run it and hand the result back

Keep the tools in a dictionary so a name from the model can be looked up. Then run the one it
asked for, add the result to the conversation, and call the model again.

```python
TOOLS = {"get_time": get_time}

result = TOOLS[request["tool"]](**request.get("args", {}))
print("tool result:", result)

messages.append({"role": "assistant", "content": reply})
messages.append({"role": "user", "content": f"Tool result: {result}"})

print(call_model(messages))
```

Now the answer is correct, because the real time is sitting in the conversation for the model
to read. Look at the four messages that made it happen:

```python
messages
```

1. `system`: the rules, including how to ask for a tool
2. `user`: the question
3. `assistant`: the model's request
4. `user`: the result your code produced

This is the pattern behind every agent. **The model decides what to do. Your code does it.**
The model never touches the clock, the network, or your files. It only ever writes text, and
your program chooses what that text is allowed to cause.

> Many model APIs have a built-in `tools` parameter that formats these requests for you. It
> is the same idea with a stricter format. Building it by hand first shows there is no magic
> underneath.

## Your turn

Add a second tool. `calculate` is written for you; register it and describe it to the model.
Edit the two `FIXME` lines, then run the cell.

```python
def calculate(expression):
    """Evaluate arithmetic such as '17 * (4 + 9)'."""
    if not re.fullmatch(r"[0-9+\-*/(). ]+", expression):
        raise ValueError("only numbers and + - * / ( ) are allowed")
    return str(eval(expression, {"__builtins__": {}}))


TOOLS = {"get_time": get_time}  # FIXME: add "calculate"

SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}
FIXME: describe calculate here, with its args

After you receive a tool result, answer the user in plain text."""

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "What is 1234 * 5678?"},
]
reply = call_model(messages)
print(reply)
```

```check
task: "`calculate` is registered as a tool, described in the system prompt, and the model asked for it."
python: |
  assert set(TOOLS) >= {"get_time", "calculate"}, f"TOOLS should hold both tools, got {sorted(TOOLS)}"
  assert "FIXME" not in SYSTEM_PROMPT, "Replace the FIXME line in SYSTEM_PROMPT."
  assert "calculate" in SYSTEM_PROMPT and "expression" in SYSTEM_PROMPT, "Describe calculate and its `expression` argument in SYSTEM_PROMPT."
  assert TOOLS["calculate"]("2 * (3 + 4)") == "14"
  assert parse_tool_request('{"tool": "calculate", "args": {"expression": "1+1"}}')["tool"] == "calculate"
  assert parse_tool_request("The answer is 4.") is None
  request = parse_tool_request(reply)
  assert request and request["tool"] == "calculate", f"The model did not ask for calculate. It said: {reply!r}"
hint: "Add `\"calculate\": calculate` to TOOLS, and a line such as `- calculate: evaluate arithmetic. args: {\"expression\": \"2 * (3 + 4)\"}` to the prompt."
success: "The model now chooses between two tools, and your code is ready to run either."
```

```solution
def calculate(expression):
    """Evaluate arithmetic such as '17 * (4 + 9)'."""
    if not re.fullmatch(r"[0-9+\-*/(). ]+", expression):
        raise ValueError("only numbers and + - * / ( ) are allowed")
    return str(eval(expression, {"__builtins__": {}}))


TOOLS = {"get_time": get_time, "calculate": calculate}

SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}
- calculate: evaluate arithmetic. args: {"expression": "2 * (3 + 4)"}

After you receive a tool result, answer the user in plain text."""

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "What is 1234 * 5678?"},
]
reply = call_model(messages)
print(reply)
```

Notice that `calculate` refuses anything except digits and arithmetic symbols before it
evaluates. The expression comes from the model, and a tool should never trust its input. You
will see why that matters in the last lesson.

## What you have

A model that can ask for help, and code that provides it. Two tools so far, both answering
from inside your program.

## Next

Continue to [Web Search](/learn/web-search).
