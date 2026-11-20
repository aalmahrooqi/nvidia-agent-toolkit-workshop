---
title: "Start Here"
subtitle: "How this notebook works, and the one key you need"
minutes: 5
kernel: python
---

In this course you build an AI agent from nothing, one small piece at a time. Every lesson
starts with something the agent cannot do yet, and ends with the few lines of Python that fix
it. By the last lesson you will have written a working agent yourself and will know exactly
what is inside it, because you typed every part.

There is no framework and nothing hidden. The whole agent ends up at about 70 lines.

```agent-map
step: none
```

## What you will build

| Lesson | The problem | What you add |
|---|---|---|
| A Bare LLM | Nothing yet | One request to a model |
| Memory | It forgets what you just said | The conversation history |
| A First Tool | It cannot know what time it is | A way to ask your code for help |
| Web Search | It cannot know what happened today | A search tool |
| The Agent Loop | One tool call is not enough | A loop: think, act, look, repeat |
| When the Loop Goes Wrong | It crashes or never stops | Error handling and a step limit |
| Why a Sandbox | It will do whatever it is told | Nothing in Python can fix this |

## How this page works

Each box is a **cell**, like a cell in a Jupyter notebook.

- Press **Run** (or Shift+Enter) to run it. The output appears underneath.
- You can edit a cell before running it. **Reset** puts the original back.
- Cells in a lesson share one Python session. A variable you create in one cell is still
  there in the next, so run them in order, top to bottom.
- **Restart kernel**, at the top of the page, clears every variable in this lesson.

Try it. Run this cell:

```python
greeting = "Cells share state"
len(greeting)
```

Now run this one. It uses `greeting` from the cell above:

```python
greeting.upper()
```

You can also edit the cells and add your own code, for example, try fixing this syntax error:
```python
print("Hey, welcome to the course!)
```

If you ran them in order you see `'CELLS SHARE STATE'`. Each lesson has its own session, so
variables do not carry over from one lesson to the next.

## Your NVIDIA API key

The model you will talk to is hosted by NVIDIA. Your own code sends the requests, so it needs
your API key. Generate an API key from [build.nvidia.com](https://build.nvidia.com). Paste it here:

```key
name: NVIDIA_API_KEY
label: NVIDIA API key
placeholder: nvapi-...
hint: Get one free at build.nvidia.com. It is kept in memory for this browser session only, never written to disk, and is handed to your Python cells as the `NVIDIA_API_KEY` environment variable.
```

Confirm your cells can see it. This prints whether the key is set, never the key itself:

```python
import os

"NVIDIA_API_KEY" in os.environ
```

We can view all the models that are available to us:

```python
import requests

response = requests.get(
    "https://integrate.api.nvidia.com/v1/models",
    headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"},
)
response.raise_for_status()

[model["id"] for model in response.json()["data"]]
```

```check
task: "Your Python cells can see `NVIDIA_API_KEY`."
python: |
  import os
  assert os.environ.get("NVIDIA_API_KEY", "").startswith("nvapi-"), "The key is not set yet. Paste it in the field above and press Save."
hint: "Paste the key into the field above and press Save, then run the check again. If the server was restarted, the key has to be entered again."
success: "The key is in place. You are ready to call a model."
```

## Next

Continue to [A Bare LLM](/learn/bare-llm).
