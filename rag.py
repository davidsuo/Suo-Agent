import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings   # 改用本地模型
from langchain_community.vectorstores import Chroma

# ------------------- 配置 -------------------
CHROMA_PERSIST_DIR = "./chroma_db"
COLLECTION_NAME = "my_docs"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100

# ------------------- 初始化本地 Embedding 模型 -------------------
embeddings = HuggingFaceEmbeddings(
    model_name="shibing624/text2vec-base-chinese",  # 中文语义模型
    model_kwargs={'device': 'cpu'},                # 没有GPU就用CPU
    encode_kwargs={'normalize_embeddings': True}    # 归一化向量，提升相似度精度
)

# ------------------- 向量库对象 -------------------
vector_store = Chroma(
    collection_name=COLLECTION_NAME,
    embedding_function=embeddings,
    persist_directory=CHROMA_PERSIST_DIR,
)

# ------------------- 工具函数 -------------------
def load_and_split_document(file_path: str):
    if file_path.endswith(".txt"):
        loader = TextLoader(file_path, encoding="utf-8")
    elif file_path.endswith(".pdf"):
        loader = PyPDFLoader(file_path)
    else:
        raise ValueError(f"不支持的文件类型: {file_path}")

    documents = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
    )
    chunks = text_splitter.split_documents(documents)
    return chunks

def add_document_to_store(file_path: str):
    chunks = load_and_split_document(file_path)
    vector_store.add_documents(chunks)
    vector_store.persist()
    return len(chunks)

def search_similar(query: str, k: int = 3):
    docs = vector_store.similarity_search(query, k=k)
    context = "\n\n".join([d.page_content for d in docs])
    return context