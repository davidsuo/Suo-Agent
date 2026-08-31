import { useState, useRef, useEffect } from 'react';
import { Layout, Menu, Input, Button, Avatar, message as antMessage } from 'antd';
import { UserOutlined, SendOutlined, PlusOutlined } from '@ant-design/icons';
import api from '../api/client';
import ReactMarkdown from 'react-markdown';

const { Sider, Content, Header } = Layout;

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function Chat({ user }: { user: any }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentProject, setCurrentProject] = useState('主对话');
  const [projects] = useState(['主对话']);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const sessionId = `${user.username}_${currentProject}`;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMsg = input;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);
    // 插入加载占位，提升体验
    setMessages(prev => [...prev, { role: 'assistant', content: '🔍 正在为您搜索、分析数据并计算，请稍候...' }]);

    try {
      const res = await api.post('/chat', {
        session_id: sessionId,
        query: userMsg
      });
      
      const fullText = res.data.answer || '抱歉，我暂时无法回答。';
      // 替换掉加载占位
      setMessages(prev => {
        const newMessages = [...prev];
        newMessages[newMessages.length - 1] = { role: 'assistant', content: fullText };
        return newMessages;
      });
    } catch (err) {
      antMessage.error('AI 响应超时，请重试');
      // 移除加载占位并报错
      setMessages(prev => prev.filter(msg => msg.content !== '🔍 正在为您搜索、分析数据并计算，请稍候...'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider theme="light" width={250} style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: 16, fontWeight: 'bold' }}>🚀 企业AI原生系统</div>
        <div style={{ padding: '0 16px', marginBottom: 8 }}>
          <Button block icon={<PlusOutlined />}>新建对话窗</Button>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[currentProject]}
          items={projects.map(p => ({ key: p, label: p }))}
          onClick={({ key }) => setCurrentProject(key)}
        />
      </Sider>
      
      <Layout>
        <Header style={{ background: '#fff', display: 'flex', alignItems: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <Avatar icon={<UserOutlined />} />
          <span style={{ marginLeft: 12 }}>{user.display_name} ({user.department})</span>
        </Header>

        <Content style={{ padding: 20, overflowY: 'auto', background: '#f5f5f5' }}>
          {messages.map((msg, idx) => (
            <div key={idx} style={{ 
              display: 'flex', 
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
              marginBottom: 12 
            }}>
              <div style={{
                maxWidth: '70%',
                padding: '10px 16px',
                borderRadius: 8,
                background: msg.role === 'user' ? '#1890ff' : '#fff',
                color: msg.role === 'user' ? '#fff' : '#333',
                boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                whiteSpace: 'pre-wrap'
              }}>
                {msg.role === 'assistant' ? (
                  <div className="markdown-body">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </Content>

        <div style={{ padding: 20, background: '#fff', borderTop: '1px solid #f0f0f0' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={handleSend}
            disabled={loading}
            placeholder={loading ? "AI 正在处理复杂任务，请稍候..." : "发消息或按住空格说话，松开发送..."}
            autoSize={{ minRows: 1, maxRows: 4 }}
            style={{ marginBottom: 8 }}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading} disabled={loading} style={{ float: 'right' }}>
            发送
          </Button>
        </div>
      </Layout>
    </Layout>
  );
}