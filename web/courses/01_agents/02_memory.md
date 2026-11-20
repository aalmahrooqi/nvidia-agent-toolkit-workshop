---
title: "Memory"
subtitle: "The model forgets everything, so you remember for it"
minutes: 15
kernel: python
---

Chat assistants seem to remember what you said a moment ago. The model does not. In this
lesson you watch it forget, and then build the memory yourself.

## What you will do

- See that every call to the model starts from nothing
- Keep a conversation history and send it with each request
- See what that history costs as it grows

## Setup

Each lesson has its own Python session, so start by bringing back what you built. This is
`call_model` from the last lesson, unchanged.

```python
import os

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

## 1. Watch it forget

Tell the model your name:

```python
print(call_model([{"role": "user", "content": "Hi! My name is Ada."}]))
```

Now ask what it is:

```python
print(call_model([{"role": "user", "content": "What is my name?"}]))
```

It does not know. Nothing is broken. The model is **stateless**: each request is handled on
its own, and nothing from the first call exists in the second. The only thing the model can
see is the list of messages in the request it is answering right now.

## 2. Remember for it

If the model only sees the messages you send, then memory is simple: send the earlier
messages again. Keep a list, add every message to it, and send the whole list each time.

```python
history = []


def chat(text):
    """Send one user message, with everything said so far, and remember the reply."""
    history.append({"role": "user", "content": text})
    reply = call_model(history)
    history.append({"role": "assistant", "content": reply})
    return reply
```

There is a third role here. `assistant` marks what the model itself said, so that it can read
its own earlier replies.

Try the same conversation again:

```python
print(chat("Hi! My name is Ada."))
```

```python
print(chat("What is my name?"))
```

This time it knows. Look at what was actually sent to make that happen:

```python
history
```

That list is the entire memory. There is no memory inside the model; there is a list in your
program that you resend. When a chat product "remembers" a conversation, this is what it is
doing.

## 3. What memory costs

Every turn resends everything before it, so each request is longer than the last.

```python
def history_size():
    characters = sum(len(message["content"]) for message in history)
    return f"{len(history)} messages, about {characters // 4} tokens sent on the next turn"


chat("Tell me one fact about the moon.")
chat("And one about Mars.")
history_size()
```

Two things follow from this:

- **Cost and speed.** You pay for those tokens again on every turn, and longer requests are
  slower.
- **A hard limit.** A model can only read so much at once, called its context window. A long
  enough conversation will not fit.

The simplest fix is to forget on purpose and send only the most recent messages.

## Your turn

Complete `recent` so it returns only the last `keep` messages of a list.

```python
def recent(messages, keep=2):
    """Return only the last `keep` messages."""
    return messages  # FIXME: return just the last `keep` messages


short_history = recent(history, keep=2)
len(short_history)
```

```check
task: "`chat` remembers the conversation, and `recent` keeps only the last messages."
python: |
  assert len(history) >= 4, "Run the chat cells in order so the history has several messages."
  roles = [message["role"] for message in history]
  assert roles[0] == "user" and "assistant" in roles, f"Unexpected roles: {roles}"
  sample = [{"role": "user", "content": str(n)} for n in range(5)]
  assert recent(sample, keep=2) == sample[-2:], "recent(messages, keep=2) should return the last 2 messages."
  assert recent(sample, keep=4) == sample[-4:], "recent should respect the keep argument."
  assert len(sample) == 5, "recent should not change the list it is given."
hint: "A list slice does it: `messages[-keep:]` is the last `keep` items."
success: "You built conversation memory, and a way to keep it from growing forever."
```

```solution
def recent(messages, keep=2):
    """Return only the last `keep` messages."""
    return messages[-keep:]


short_history = recent(history, keep=2)
len(short_history)
```

Dropping old messages is crude: the model really does forget them. Real systems summarize
old turns or store facts somewhere searchable. Those are refinements of the same idea, which
is that **you decide what the model gets to see**.

## What you have

`call_model`, plus a list. The model can now hold a conversation.

It still only knows what it learned in training and what you tell it. Next you will ask it
something neither of those covers.

## Next

Continue to [A First Tool](/learn/first-tool).
