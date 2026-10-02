"""Read-only UTA check; prints only redacted capability checks, never credentials."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.config import load_settings
from app.bitget import BitgetDemoClient
from app.uta import UtaReadiness


async def main():
    client = BitgetDemoClient(load_settings())
    try:
        result = await UtaReadiness(client).check()
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'read_access':False,'error_type':type(exc).__name__},ensure_ascii=False))
        return 1
    finally:
        await client.close()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
