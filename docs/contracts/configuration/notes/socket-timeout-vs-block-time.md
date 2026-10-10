# Socket timeout and block time

The socket timeout (10 s) must be longer than the block time (2 s).

| Timer | Set on | Meaning | Value |
|---|---|---|---|
| Block time | Redis | How long Redis waits for a new entry before it answers "nothing" | 2 s |
| Socket timeout | The client | How long the client waits for any answer before it raises an error | 10 s |

```
Correct: socket timeout 10 s, block time 2 s

0 s           2 s                                  10 s
├─────────────┼────────────────────────────────────┤
│ Redis waits │ Redis answers "nothing"            │ socket timeout
│             │ → empty list, no error, read again │ (not reached)


Wrong: socket timeout 1 s, block time 2 s

0 s       1 s       2 s
├─────────┼─────────┤
│ Redis   │ client raises TimeoutError ✗
│ waits   │         │ Redis would answer "nothing" here
```

- With the correct values, an idle read returns an empty list and raises nothing.
- With the wrong values, every idle read raises a timeout error, although Redis is healthy.
- The 8 s margin allows for a slow network. A larger socket timeout makes a dead Redis take longer to detect.

See [queue.md](../../queue.md).
