# 我的AI招聘Agent

这是一个能自动分析简历、爬取岗位、并智能匹配的AI Agent。

## 如何运行

1.  **安装依赖:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **输入你自己的deepseek api 文件:**
    在ai_agent_server.py中输入你自己的api：
    ```
    MY_DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" # 确保这里是你的 Key
    ZHAOPIN_COOKIE我已经偷好放进去了
    ```

3.  **运行索引 (如果需要):**
    ```bash
    python vector_indexer.py
    ```

4.  **启动服务器:**
    ```bash
    uvicorn ai_agent_server:app --reload
    ```
