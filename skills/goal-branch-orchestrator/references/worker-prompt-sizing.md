# Worker Prompt Sizing

Worker packets must fit the smallest intended worker context. Spark fallback has a local context window of about 128k tokens, so keep packets below roughly 80k-100k total input context.

## Good Worker Packet

- one task objective;
- exact owned paths;
- at most a few read-first files;
- exact commands to run;
- explicit stop conditions;
- JSON status output;
- no broad architecture exploration unless that is the whole small task.

## Bad Worker Packet

- asks for a whole branch implementation;
- tells the worker to inspect the whole repo;
- gives several unrelated modules;
- relies on chat history;
- has no falsifiable DoD;
- asks the worker to decide branch strategy.

If a packet does not fit this envelope, split it before dispatch.
