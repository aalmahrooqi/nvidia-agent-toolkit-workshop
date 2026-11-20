---
title: "Web Search"
subtitle: "Answers grounded in facts the model was never trained on"
minutes: 20
kernel: python
---

A model's knowledge stops on the day its training data was collected. Ask about anything
after that and it either says it does not know or, worse, answers confidently from stale
information. In this lesson you give it a tool that reaches the live web.

## What you will do

- See the model answer from out-of-date knowledge
- Write a search tool using the Tavily API
- Have the model answer from search results and say where they came from

## Setup

Everything from the last lesson: `call_model`, the two tools, and the request parser.

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

## 1. Ask about something recent

```python
question = "What is the newest stable version of Python, and when was it released?"
print(call_model([{"role": "user", "content": question}]))
```

Whatever it says, it is describing the world as of its training data. It has no way to know
about a release that came later, and it may not mention that. This is the practical meaning
of "hallucination" for most real uses: not wild invention, but a confident answer that was
true once.

## 2. A search key (optional)

The search tool uses [Tavily](https://tavily.com), a search API made for this purpose. A free
key raises your rate limit. Without one, Tavily allows a small number of requests, which may
be enough for this lesson.

```key
name: TAVILY_API_KEY
label: Tavily API key (optional)
placeholder: tvly-...
optional: true
hint: Free at app.tavily.com. Like the NVIDIA key, it stays in memory for this browser session only.
```

## 3. Write the search tool

A search tool is another function that returns text. It sends the query to Tavily and turns
the top results into a short block the model can read.

```python
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
```

Call it yourself before giving it to the model. A tool is ordinary code, and you can test it
like any other function:

```python
print(web_search("newest stable Python version release date"))
```

If this fails with a `401` or `429` error, add a Tavily key above and run the cell again.

## 4. Give it to the model

Register the tool and describe it. The prompt also tells the model to name its sources, so
you can check where an answer came from.

```python
TOOLS = {"get_time": get_time, "calculate": calculate, "web_search": web_search}

SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}
- calculate: evaluate arithmetic. args: {"expression": "2 * (3 + 4)"}
- web_search: search the web for current information. args: {"query": "..."}

Use web_search for anything recent or anything you are not sure of.
After you receive a tool result, answer the user in plain text.
When your answer uses a search result, include the URL it came from."""
```

Ask the same question, this time following the request through by hand as you did before:

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": question},
]
reply = call_model(messages)
request = parse_tool_request(reply)
print("the model asked for:", request)
```

```python
result = TOOLS[request["tool"]](**request.get("args", {}))
messages.append({"role": "assistant", "content": reply})
messages.append({"role": "user", "content": f"Tool result: {result}"})

answer = call_model(messages)
print(answer)
```

The answer now comes from pages that exist today, and it tells you which ones. This is called
**grounding**: the model's job changes from recalling facts to reading the facts you put in
front of it. It is far better at the second job.

Grounding does not make an answer true. The model can still misread a page, and a page can be
wrong. What it gives you is an answer you can check.

```check
task: "`web_search` is a registered tool, and the model used it to answer."
python: |
  assert set(TOOLS) >= {"get_time", "calculate", "web_search"}, f"TOOLS is missing a tool: {sorted(TOOLS)}"
  assert "web_search" in SYSTEM_PROMPT, "Describe web_search in SYSTEM_PROMPT."
  assert request and request["tool"] == "web_search", f"The model did not ask for web_search: {request!r}"
  assert any(m["content"].startswith("Tool result:") for m in messages), "Run the cell that hands the result back to the model."
  assert isinstance(answer, str) and answer.strip(), "Run the cell that produces `answer`."
hint: "Run the cells in section 4 in order. If the search itself fails, add a Tavily key in section 2."
success: "The model answered from live search results instead of its training data."
```

## What you have

Three tools, and a model that knows when to reach for each. But look at how you ran them: by
hand, one cell at a time, for exactly one tool call. A real question often needs several.

## Next

Continue to [The Agent Loop](/learn/agent-loop).
