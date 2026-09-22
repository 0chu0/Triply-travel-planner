"""测试交通规划 Subagents 系统"""
import asyncio
import pytest
from app.agents.subagents.transport_coordinator import create_transport_coordinator


@pytest.mark.asyncio
async def test_flight_query():
    """测试航班查询"""

    coordinator = await create_transport_coordinator()

    print("\n=== 测试航班查询 ===")

    response = await coordinator.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": "我想从北京飞到上海，明天出发，请帮我查询航班。"
            }
        ]
    })

    print(f"\n协调器响应：\n{response['messages'][-1].content}")

    content = response['messages'][-1].content
    assert "航班" in content or "flight" in content.lower()

    print("\n✅ 航班查询测试完成")



@pytest.mark.asyncio
async def test_driving_route():
    """测试自驾路线"""

    coordinator = await create_transport_coordinator()

    print("\n=== 测试自驾路线 ===")

    response = await coordinator.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": "我打算自驾从北京到上海，帮我规划一下路线。"
            }
        ]
    })

    print(f"\n协调器响应：\n{response['messages'][-1].content}")

    print("\n✅ 自驾路线测试完成")


if __name__ == "__main__":
    async def main():
        await test_flight_query()
        await test_driving_route()


    asyncio.run(main())