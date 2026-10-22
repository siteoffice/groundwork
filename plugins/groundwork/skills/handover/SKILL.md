---
name: handover
description: Use when the user says "handover", "hand over", "wrap up this chat", "prepare the next chat", "maak een overdracht", "draag over naar een nieuwe chat", or a conversation has grown long and needs to continue in a new one. Ends the chat with a short handover document so the next chat starts fresh, cheap and with nothing lost.
---

# Handover

A long chat gets more expensive every turn and the model starts losing earlier
decisions. The fix is to end the chat with one short document and start the next
chat from that document instead of from the whole history.

## What to produce

One markdown document, under 300 words, with exactly these sections:

1. **Goal**: what this work is trying to achieve, one or two lines.
2. **Done**: what is finished, as facts, not activities.
3. **Decisions and why**: every choice the next chat must not reopen, each with
   its reason in the same line.
4. **Open items**: what is not finished, and anything known to be broken.
5. **Next step**: the single first thing the next chat should do.
6. **Files**: full paths of every file created or changed, if any.

Verification: all six sections are present, the document is under 300 words, every
decision carries its reason inline, and Next step names exactly one action, not a
list of options.

## Rules

- Write the handover in the language of the conversation (Dutch stays Dutch,
  English stays English).
- Facts only. No summary of the conversation flow, no praise, no filler.
- Decisions carry their why. A decision without its reason gets reopened.
- If a working folder is connected, save the document as
  `handovers/YYYY-MM-DD_<topic>.md` in it and tell the user the path.
  Otherwise print it in the chat for copying.
- End by telling the user: start a new chat, paste (or attach) the handover,
  and state the next step as the first message.

This pattern is vendor agnostic: it works with any AI assistant, because every
assistant re-reads its context and every assistant starts a new conversation
empty.
