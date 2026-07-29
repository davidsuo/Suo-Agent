# 安装：pip install streamlit
import streamlit as st
import pandas as pd

# 页面标题
st.title("数据看板 Demo")
# 文本输入框
name = st.text_input("请输入姓名")
# 滑块
score = st.slider("分数", 0, 100)
# 展示表格
df = pd.DataFrame({"姓名":[name], "分数":[score]})
st.dataframe(df)