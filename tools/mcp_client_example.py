"""Official SDK client example. Lists tools/voices; generates only with --text."""
import argparse
import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text', help='Optional script to submit asynchronously')
    parser.add_argument('--voice-id')
    args = parser.parse_args()
    key = os.environ.get('VOICEBENCH_API_KEY', '').strip()
    url = os.environ.get('VOICEBENCH_MCP_URL', '').strip()
    if not key or not url:
        raise SystemExit('Set VOICEBENCH_API_KEY and VOICEBENCH_MCP_URL.')
    async with httpx.AsyncClient(headers={'Authorization': 'Bearer ' + key}) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print('Tools:', ', '.join(tool.name for tool in (await session.list_tools()).tools))
                result = await session.call_tool('list_voices', {})
                print(result.content[0].text)
                if args.text:
                    result = await session.call_tool('generate_speech', {'text': args.text, 'voice_id': args.voice_id})
                    print(result.content[0].text)
                    value = json.loads(result.content[0].text)
                    if not result.isError:
                        print('Poll get_job with request_id:', value['id'])


if __name__ == '__main__':
    asyncio.run(main())
