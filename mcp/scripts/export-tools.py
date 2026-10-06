import argparse
import asyncio
import json
from pathlib import Path

from rustdesk_mcp.app import make_server
from rustdesk_mcp.config import Config


async def main():
    parser=argparse.ArgumentParser(description='Export public tool schemas without starting a bridge or reading credentials')
    parser.add_argument('--output',type=Path,default=Path('mcp/docs/tools-schema.json'))
    args=parser.parse_args()
    server=make_server(Config(Path('missing.exe'),Path('unused')))
    tools=await server.list_tools()
    args.output.write_text(json.dumps({'version':'0.2.0','count':len(tools),'tools':[tool.model_dump(exclude_none=True) for tool in tools]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Exported {len(tools)} tool schemas to {args.output}')


asyncio.run(main())
