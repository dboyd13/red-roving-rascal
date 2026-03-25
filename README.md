# red-roving-rascal

A lightweight API testing and evaluation toolkit.

## Install

```bash
pip install red-roving-rascal
```

## Quick Start

```python
from rascal.client import RascalClient

client = RascalClient(endpoint="https://your-api.example.com")
result = client.run_job(inputs=["hello world"], target="my-service")
print(result)
```
