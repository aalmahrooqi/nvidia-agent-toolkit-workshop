---
title: "A Bare LLM"
subtitle: "One request in, one reply out"
minutes: 15
kernel: python
---

An agent is built around a large language model (LLM). Before adding anything to it, look at
what a model is on its own: a service that takes text and returns text. That is the entire
interface. Everything else in this course is code you write around it.

## What you will do

- Send one request to a hosted model and read the raw response
- Wrap that request in a small function, `call_model`
- Use a system prompt to change how the model behaves

## 1. Send one request

Calling a model is an ordinary HTTP request. There is a URL, your API key in a header, and a
JSON body that names the model and carries a list of **messages**.

```python
import os

import httpx

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

response = httpx.post(
    API_URL,
    headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"},
    json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say hello in five words."}],
        "max_tokens": 4096,
        "stream": False,
    },
    timeout=120,
)
response.status_code
```

`200` means it worked. If you see `401`, the key is wrong: go back to
[Start Here](/learn/start-here) and save it again.

## 2. Read the raw response

Look at everything that came back before hiding it behind a function:

```python
data = response.json()
data
```

Three parts matter:

| Field | What it is |
|---|---|
| `choices[0].message.content` | The model's reply. This is the text you want. |
| `choices[0].finish_reason` | Why it stopped. `stop` means it finished; `length` means it ran out of `max_tokens`. |
| `usage` | How many tokens went in and came out. Tokens are pieces of words, and they are what you pay for. |

Some models think before they answer. If you see a `reasoning_content` field next to
`content`, that is the model's working, and `content` is still the answer.

Pull the reply out:

```python
data["choices"][0]["message"]["content"]
```

## 3. Wrap it in a function

You will make this call many times, so give it a name. `call_model` takes a list of messages
and returns the reply text. This one function is the only place in the whole course that
talks to the model.

```python
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

`temperature` controls randomness. A low value such as `0.2` makes replies more repeatable,
which is what you want while learning.

Try it:

```python
reply = call_model([{"role": "user", "content": "Name three planets."}])
print(reply)
```

## 4. Steer it with a system prompt

Each message has a **role**. So far you have used `user`, which is you. A `system` message
comes first and sets the rules the model should follow for the whole conversation.

```python
messages = [
    {"role": "system", "content": "You are a pirate. Answer in one short sentence."},
    {"role": "user", "content": "Name three planets."},
]
print(call_model(messages))
```

Same question, different behavior. The system prompt is how you will later tell the model
which tools it has and how to ask for them.

## Your turn

Change the system prompt in this cell so the model replies with **exactly one word**, then
run it:

```python
messages = [
    {"role": "system", "content": "FIXME: tell the model to answer with exactly one word."},
    {"role": "user", "content": "What is the capital of France?"},
]
one_word = call_model(messages)
print(one_word)
```

```check
task: "`call_model` works, and `one_word` holds a one-word answer."
python: |
  assert callable(call_model), "Run the cell that defines call_model."
  assert isinstance(reply, str) and reply.strip(), "Run the 'Name three planets' cell first."
  assert "FIXME" not in messages[0]["content"], "Replace the FIXME in the system prompt."
  assert len(one_word.strip().split()) == 1, f"Expected one word, got: {one_word!r}"
hint: "A system prompt such as `Answer with exactly one word and no punctuation.` works. If the model still adds words, make the instruction firmer."
success: "You sent requests to a model and shaped its behavior with a system prompt."
```

```solution
messages = [
    {"role": "system", "content": "Answer with exactly one word and no punctuation."},
    {"role": "user", "content": "What is the capital of France?"},
]
one_word = call_model(messages)
print(one_word)
```

## What you have

A function that sends text to a model and returns text. It is about ten lines, and it is
everything a "bare LLM" can do.

It has a serious limit, which you are about to run into.

## Next

Continue to [Memory](/learn/memory).
