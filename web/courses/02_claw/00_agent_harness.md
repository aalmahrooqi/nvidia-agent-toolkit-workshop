---
title: "From Your Agent to an Agent Harness"
subtitle: "What you built has a name, and this course puts a boundary around it"
minutes: 20
kernel: python
# A stand-in plays the model on this page, so its cells are given no API key.
keys: []
---

In *Agents from Scratch* you wrote about 70 lines of Python that turn a model into an agent.
This course runs a full-size agent inside a sandbox, using three products: OpenClaw,
OpenShell, and NemoClaw. That can look like a change of subject. It is the same subject, one
level up. This lesson shows where the code you wrote sits among those three names.

```stack-map
```

## What you will do

- Put a name on the code you wrote: an **agent harness**
- Find something your harness cannot do yet, and add it in five lines
- See what a production harness such as OpenClaw adds, and when you would pick which one
- Place OpenShell and NemoClaw around the harness

## 1. What you built has a name

A model takes text and returns text. You saw that in [A Bare LLM](/learn/bare-llm), and it
never changed. Everything else you wrote was code *around* the model: it keeps the
conversation, describes the tools, runs them, and decides when to stop. That surrounding code
is called an **agent harness**.

> **Agent = model + harness.** The model supplies the judgement. The harness supplies
> everything else: "every piece of code, configuration, and execution logic that isn't the
> model itself", in the words of the
> [LangChain article](https://blog.langchain.com/the-anatomy-of-an-agent-harness/) the formula
> comes from.

Here is your whole agent in one cell, with each part of the harness labelled. Two things
are different from before. A small stand-in plays the model, so this page needs no API key.
And `run_agent` can be handed an existing `messages` list, which you will use in section 2.

```python
import json
import re
from datetime import datetime


# ---- THE MODEL: text in, text out. The only part that is not harness. -----------------
def stand_in_model(messages):
    """Play the model, so this page needs no API key. It knows three tricks."""
    said = [message["content"] for message in messages if message["role"] == "user"]
    latest = said[-1]
    if latest.startswith("Tool result:"):
        return f"Here is what I found: {latest.removeprefix('Tool result: ')}"
    if "time" in latest.lower():
        return '{"thought": "I need a clock for that.", "tool": "get_time", "args": {}}'
    sums = re.findall(r"[0-9][0-9+\-*/(). ]*[0-9)]", latest)
    if sums:
        args = {"expression": sums[0]}
        return json.dumps({"thought": "That is arithmetic.", "tool": "calculate", "args": args})
    if "what is my name" in latest.lower():
        names = re.findall(r"my name is (\w+)", " ".join(said), re.IGNORECASE)
        return f"Your name is {names[-1]}." if names else "I do not know your name."
    return "Noted."


# ---- HARNESS: tools, the functions the model can ask for ------------------------------
def get_time():
    """The current local date and time."""
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def calculate(expression):
    """Evaluate arithmetic such as '17 * (4 + 9)'."""
    if not re.fullmatch(r"[0-9+\-*/(). ]+", expression):
        raise ValueError("only numbers and + - * / ( ) are allowed")
    return str(eval(expression, {"__builtins__": {}}))


TOOLS = {"get_time": get_time, "calculate": calculate}

# ---- HARNESS: instructions, who the agent is and how to ask for a tool ----------------
SYSTEM_PROMPT = """You are a helpful assistant that can use tools.

To use a tool, reply with ONLY a JSON object and nothing else:
{"thought": "<why you need this tool>", "tool": "<tool name>", "args": {<arguments>}}

Tools:
- get_time: the current local date and time. args: {}
- calculate: evaluate arithmetic; write powers with **. args: {"expression": "2 * (3 + 4)"}

Use one tool at a time. After each tool result, decide whether you need another tool.
When you have everything you need, reply with the final answer in plain text."""


# ---- HARNESS: the tool protocol, turning the model's text into a request --------------
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


# ---- HARNESS: guardrails, so a bad request becomes text the model can read ------------
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


# ---- HARNESS: memory and the loop. Resend everything, act, look, repeat. --------------
def run_agent(question, max_steps=6, model=stand_in_model, messages=None):
    """Answer a question, using tools as many times as the model asks for them."""
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})

    for step in range(1, max_steps + 1):
        reply = model(messages)
        messages.append({"role": "assistant", "content": reply})
        request = parse_tool_request(reply)

        if request is None:
            return reply

        result = run_tool(request)
        print(f"step {step}: {request.get('thought', '')}")
        print(f"   action: {request.get('tool')}({request.get('args', {})})")
        print(f"   result: {result[:200]}")

        messages.append({"role": "user", "content": f"Tool result: {result}"})

    return "Stopped: the step limit was reached before the agent finished."


print(run_agent("What time is it?"))
```

Count the labels. Five say `HARNESS` and one says `THE MODEL`. The stand-in model is a few
`if` statements with no intelligence at all, and the agent still worked: the harness kept the
conversation, ran the tool, fed the result back, and stopped. Change the question to
`"What is 17 * 3?"` and the same harness runs the other tool.

That is why the harness gets its own name. It is the part you can build, test, and replace
without touching the model, and it does not care which model it is given. Hand `run_agent`
the `call_model` you wrote in the last course and nothing else in this cell would change.

## 2. What your harness is missing

Tell the agent something, then ask about it:

```python
print(run_agent("My name is Sam."))
print(run_agent("What is my name?"))
```

It forgot. Each call to `run_agent` starts a new `messages` list, so nothing carries over
from one question to the next. You solved this once already: in [Memory](/learn/memory),
`chat` kept one `history` list and resent it on every call. When you wrote the loop,
`run_agent` built its own list, and that memory was left behind.

Keeping it is a job for the harness. Put it back, and this time give each conversation a name:

```python
SESSIONS = {}


def chat(question, session="main"):
    """Run the agent inside a named session that keeps its conversation."""
    history = SESSIONS.setdefault(session, [{"role": "system", "content": SYSTEM_PROMPT}])
    return run_agent(question, messages=history)


print(chat("My name is Sam."))
print(chat("What is my name?"))
print(chat("What is my name?", session="someone-else"))
```

That is a **session**: five lines, and the agent now remembers within `main` while
`someone-else` stays separate. It is still only a Python dictionary. Restart the kernel and
every session is gone.

A production harness is this, carried all the way through. Every row below is a piece you
already wrote, grown up:

| Piece | In your harness | In a production harness |
|---|---|---|
| Model call | One function, one provider, one model | Several providers and models, chosen in configuration |
| Memory | A list. `run_agent` starts a new one for every question | Sessions saved on disk, summarized when they get too long, and notes the agent writes to files so it can find them later |
| Tools | A few functions in a dictionary, described by hand in the prompt | Built-in tools for files, commands, and the web, plus **skills** you install: folders of instructions that teach the agent when and how to use a tool |
| Tool requests | JSON in plain text, found with a regular expression | The model API's own tool-calling format |
| Instructions | One `SYSTEM_PROMPT` string | Files in a workspace, such as OpenClaw's `SOUL.md`, that say who the agent is and how it behaves |
| The loop | `for step in range(...)` | The same loop, able to hand part of a job to another agent |
| Guardrails | Errors as text, a step limit | The same, plus asking a person before a risky action |
| Who starts it | You, pressing **Run** | A chat app, a terminal, a web dashboard, or a schedule that runs while you are away |

Nothing in the right-hand column is a new idea. The difference between your agent and a
production one is size, not kind.

## 3. Ready-made harnesses

You could keep growing your own, and sometimes that is the right call. There are three ways
to get a harness:

| Way | What you get | Choose it when |
|---|---|---|
| Write your own | Full control, and every line to maintain. This is what you did in *Agents from Scratch*. | You are learning, or the job is small and fixed |
| Use a framework | The loop, memory, and tool wiring as a library that you assemble in your own code | The agent is one part of an application you are building |
| Run a ready-made harness | A finished agent that you configure instead of code | You want a capable assistant or coding agent working today |

This course takes the third way. NemoClaw can install any of these three:

| Harness | Made by | What it is | Choose it when |
|---|---|---|---|
| [OpenClaw](https://openclaw.ai) | The OpenClaw Foundation, as open source | An always-on assistant. You reach it from a terminal, a web dashboard, or chat apps, and it has skills, memory, and scheduled turns. | You want an assistant that keeps running and meets you where you already are. It is NemoClaw's default and **the one this course uses**. |
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs/) | Nous Research, as open source | Another always-on agent, which its makers describe as self-improving: it writes new skills from experience and improves them as it uses them. It also offers an OpenAI-compatible API, so other programs can talk to it. | You want an agent that builds up its own skills over time, or you want to call the agent from your own software. |
| [Deep Agents Code](https://docs.langchain.com/oss/python/deepagents/code/overview) | LangChain, as open source | A coding agent you drive from a terminal with the `dcode` command. It hands work to sub-agents, uses skills and memory, and asks before it changes files or runs commands. It has no dashboard and no chat apps. | The job is writing and changing code, not being a general assistant. |

All three are the loop you wrote in [The Agent Loop](/learn/agent-loop). LangChain says so
directly about Deep Agents: "the same core tool calling loop as other agent frameworks, but
with built-in capabilities". They differ from each other in what they are *for*, and from
yours in how much of the table in section 2 they have filled in.

You pick one when you install. OpenClaw is the default, and the others are chosen with
`nemoclaw onboard --agent hermes` or `nemoclaw onboard --agent langchain-deepagents-code`.

> **A note on the word.** NemoClaw's documentation calls these *agent runtimes*. OpenClaw's
> own documentation uses *harness* more narrowly, for the inner loop that runs one turn,
> the same job your `run_agent` does; the rest of OpenClaw adds the sessions, chat apps,
> memory, and scheduling around it. This course uses *harness* the broad way: everything
> around the model.

## 4. What no harness can give you

A better harness makes an agent more capable. It does not make it safer. An agent with a
shell tool, access to your files, and a chat app wired in has more to lose than the one you
built, and it still reads instructions from every web page it fetches.

You saw the answer at the end of [Why a Sandbox](/learn/why-a-sandbox): the control has to
sit **outside** the agent, where nothing the model writes can reach it. That is what the
other two products in this course are for.

| Product | Its job | In the picture at the top |
|---|---|---|
| **OpenShell** | The sandbox. It runs the harness in a container and decides from outside which files it can touch and which servers it can reach. It also keeps your API key away from the agent, adding it to a model request only after the request has left. | The dashed boundary, and the two green dots where requests cross it |
| **NemoClaw** | The installer and operator. It installs a harness and OpenShell in versions that were tested together, sets up the policy and the model connection, and can rebuild the sandbox when something breaks. | The box on the left |

OpenShell has, in NVIDIA's words, "no opinions about what runs inside". That is why the same
sandbox can hold any of the three harnesses above, or the agent you wrote. It also runs
coding agents such as Claude Code and Codex with no NemoClaw involved. NemoClaw is the
opinionated part: NVIDIA describes it as an early-preview reference stack for one trusted
operator on one host.

Some writers, including the LangChain article quoted above, count a sandbox as part of the
harness. This course keeps the two apart, because here the sandbox is a separate product
that enforces its rules from outside the agent's process.

One distinction is worth carrying into the rest of the course. A harness can ask you before
it does something risky, and that is useful, but it is the agent's own code doing the asking,
so a confused model can sometimes be talked around it. When OpenShell blocks a request to a
server that is not on the approved list, nothing the agent says changes the outcome.

NemoClaw is not the agent, and OpenShell is not the harness. Each of the four things you
asked for at the end of the last course is a lesson in this one:

| In *Why a Sandbox* you wanted | In this course |
|---|---|
| The agent can write only in its own folder | A filesystem policy, in [Sandbox Policies](/learn/sandbox-policies) |
| The agent can reach only approved servers | A network policy, in [Sandbox Policies](/learn/sandbox-policies) |
| The agent never holds your API key | A placeholder inside the sandbox in place of the key, which you check in [Sandbox Policies](/learn/sandbox-policies) and again in [NemoClaw with OpenClaw](/learn/nemoclaw-openclaw) |
| The agent cannot switch any of this off | Enforcement by OpenShell, outside the agent's process, in [What Is OpenShell?](/learn/what-is-openshell) |

## Your turn

For each job, say which layer owns it. Replace every `FIXME` with `"harness"`,
`"OpenShell"`, or `"NemoClaw"`, then run the cell.

```python
owner = {
    "runs the tool the model asked for": "FIXME",
    "keeps the conversation between one message and the next": "FIXME",
    "blocks a request to a server that is not on the approved list": "FIXME",
    "adds the real API key to a model request": "FIXME",
    "installs the harness and the sandbox in matching versions": "FIXME",
    "rebuilds the sandbox when it is broken": "FIXME",
}

for job, layer in owner.items():
    print(f"{layer:10} {job}")
```

```check
task: "Every job is assigned to the layer that owns it."
python: |
  answers = {
      "runs the tool the model asked for": "harness",
      "keeps the conversation between one message and the next": "harness",
      "blocks a request to a server that is not on the approved list": "openshell",
      "adds the real API key to a model request": "openshell",
      "installs the harness and the sandbox in matching versions": "nemoclaw",
      "rebuilds the sandbox when it is broken": "nemoclaw",
  }
  assert set(owner) == set(answers), "Keep the six jobs as they are and change only the values."

  def _layer(value):
      name = "".join(letter for letter in str(value).lower() if letter.isalpha())
      return {"agentharness": "harness", "theharness": "harness"}.get(name, name)

  given = {job: _layer(layer) for job, layer in owner.items()}
  unknown = sorted({str(owner[job]) for job, name in given.items() if name not in set(answers.values())})
  assert not unknown, 'Use "harness", "OpenShell", or "NemoClaw" for every job. Not one of those: ' + ", ".join(unknown)
  wrong = [job for job, name in given.items() if name != answers[job]]
  assert not wrong, "Look again at: " + "; ".join(wrong)

  assert "chat" in globals() and "SESSIONS" in globals(), "Run the session cell in section 2."
  _probe = f"check-{len(SESSIONS)}"
  chat("My name is Quill.", session=_probe)
  _remembered = chat("What is my name?", session=_probe)
  SESSIONS.pop(_probe, None)
  assert "Quill" in _remembered, "chat() should remember within one session. Run the session cell in section 2 again."
hint: "The harness is the agent's own code: its loop and its memory. OpenShell is the boundary, so anything about blocking a request or holding a credential is its job. NemoClaw installs and repairs."
success: "You can say which layer owns a job. That question comes up in every lesson of this course."
```

```solution
owner = {
    "runs the tool the model asked for": "harness",
    "keeps the conversation between one message and the next": "harness",
    "blocks a request to a server that is not on the approved list": "OpenShell",
    "adds the real API key to a model request": "OpenShell",
    "installs the harness and the sandbox in matching versions": "NemoClaw",
    "rebuilds the sandbox when it is broken": "NemoClaw",
}

for job, layer in owner.items():
    print(f"{layer:10} {job}")
```

## What you have

- A name for what you built: an **agent harness**, the code around the model
- A way to read any agent product: find the model call, the memory, the tools, the loop, and
  the limits
- Three layers with separate jobs: the harness acts, OpenShell enforces, NemoClaw installs
  and repairs

## Next

Continue to [Set Up Your Environment](/learn/setup). Setup takes several minutes, so start it
there and read on while it runs.

**Sources:**
[The Anatomy of an Agent Harness](https://blog.langchain.com/the-anatomy-of-an-agent-harness/) (LangChain) ·
[Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) (Anthropic) ·
[Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/) (OpenAI) ·
[NemoClaw ecosystem](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/ecosystem) ·
[NemoClaw platform support](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support) ·
[Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
