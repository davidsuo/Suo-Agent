import { useState, useRef, useEffect } from 'react';
import { Layout, Menu, Input, Button, Avatar, message as antMessage, Upload, Tooltip } from 'antd';
import { UserOutlined, SendOutlined, PlusOutlined, DeleteOutlined, PaperClipOutlined, SoundOutlined } from '@ant-design/icons';
import api from '../api/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Sider, Content, Header, Footer } = Layout;

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function Chat({ user }: { user: any }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentProject, setCurrentProject] = useState('主对话');
  const [projects, setProjects] = useState(['主对话']);
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
    setMessages(prev => [...prev, { role: 'assistant', content: '🔍 正在为您搜索、分析数据并计算，请稍候...' }]);

    try {
      const res = await api.post('/chat', {
        session_id: sessionId,
        query: userMsg
      });
      const fullText = res.data.answer || '抱歉，我暂时无法回答。';
      setMessages(prev => {
        const newMessages = [...prev];
        newMessages[newMessages.length - 1] = { role: 'assistant', content: fullText };
        return newMessages;
      });
    } catch (err) {
      antMessage.error('AI 响应超时，请重试');
      setMessages(prev => prev.filter(msg => msg.content !== '🔍 正在为您搜索、分析数据并计算，请稍候...'));
    } finally {
      setLoading(false);
    }
  };

  const handleAddProject = () => {
    const newProject = prompt('请输入新对话窗口名称（如：产品部）');
    if (newProject && !projects.includes(newProject)) {
      setProjects([...projects, newProject]);
      setCurrentProject(newProject);
    }
  };

  const handleDeleteProject = () => {
    if (currentProject === '主对话') {
      antMessage.warning('主对话不能删除！');
      return;
    }
    const newProjects = projects.filter(p => p !== currentProject);
    setProjects(newProjects);
    setCurrentProject('主对话');
    setMessages([]);
  };

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider theme="light" width={250} style={{ borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 16, fontWeight: 'bold', fontSize: 18 }}>🚀 某某企业AI原生系统平台</div>
        
        <div style={{ padding: '0 16px', marginBottom: 8, display: 'flex', gap: 8 }}>
          <Button block icon={<PlusOutlined />} onClick={handleAddProject}>新建对话窗</Button>
          <Button icon={<DeleteOutlined />} onClick={handleDeleteProject} danger>删除</Button>
        </div>

        <div style={{ padding: '0 16px', marginBottom: 8 }}>
          <div style={{ marginBottom: 8 }}>对话窗列表</div>
          <Menu
            mode="inline"
            selectedKeys={[currentProject]}
            style={{ borderInlineEnd: 'none' }}
            items={projects.map(p => ({ key: p, label: p }))}
            onClick={({ key }) => {
              setCurrentProject(key);
              setMessages([]); // 切换项目时清空当前显示（真实逻辑应从后端加载历史）
            }}
          />
        </div>
      </Sider>
      
      <Layout>
        <Header style={{ background: '#fff', display: 'flex', alignItems: 'center', borderBottom: '1px solid #f0f0f0', padding: '0 20px', height: 60 }}>
          <Avatar icon={<UserOutlined />} />
          <span style={{ marginLeft: 12, fontWeight: 500 }}>{user.display_name} ({user.department})</span>
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
              }}>
                {msg.role === 'assistant' ? (
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </Content>

        <div style={{ background: '#fff', borderTop: '1px solid #f0f0f0', padding: '12px 20px' }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Upload showUploadList={false}>
              <Tooltip title="上传文件（即将支持）">
                <Button icon={<PaperClipOutlined />} />
              </Tooltip>
            </Upload>
            <Tooltip title="语音输入（长按空格，即将支持）">
              <Button icon={<SoundOutlined />} />
            </Tooltip>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={handleSend}
              disabled={loading}
              placeholder={loading ? "AI 正在处理复杂任务，请稍候..." : "发消息或按住空格说话，松开发送..."}
              autoSize={{ minRows: 1, maxRows: 4 }}
            />
            <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading} disabled={loading}>
              发送
            </Button>
          </div>
        </div>

        <Footer style={{ textAlign: 'center', color: '#888', padding: '12px 0', background: '#f5f5f5' }}>
          遨游AI星空，尽享AI快乐
        </Footer>
      </Layout>
    </Layout>
  );
}