import React,{useState,useEffect} from 'react';import{createRoot}from'react-dom/client';import{LineChart,Line,XAxis,YAxis,Tooltip,ResponsiveContainer,CartesianGrid}from'recharts';import{LayoutDashboard,ChartCandlestick,Wallet,ArrowDownToLine,ArrowUpFromLine,Shield,Globe,Menu,Activity,Search,LogOut}from'lucide-react';import './style.css';
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://zencrypto-api-backend.onrender.com';
const dict={en:{dashboard:'Overview',trade:'Trade',wallet:'Assets',deposit:'Deposit',withdraw:'Withdraw',admin:'Admin',markets:'Markets',balance:'Total portfolio value',volume:'24h Volume',change:'24h Change',order:'Place order',buy:'Buy',sell:'Sell',amount:'Amount (USDT)',price:'Reference price',submit:'Submit simulated order',history:'Recent simulated orders',noorders:'No orders yet',demo:'DEMO MODE — No real funds or orders',notice:'Real-money deposits, withdrawals and order execution are disabled until licensed integrations and security controls are complete.',depositMsg:'Deposits are disabled. No wallet addresses are issued.',withdrawMsg:'Withdrawals are disabled. No funds can be transferred.',adminMsg:'Admin dashboard preview only. Production access requires server-side authentication and role controls.',asset:'Asset',quantity:'Quantity',value:'Estimated value',status:'System status',operational:'Frontend operational',welcome:'Digital asset markets, clearly presented',market:'Market',last:'Last price',action:'Action',view:'View',pair:'Trading pair',type:'Side',time:'Time',adminOnly:'Demo analytics — not production data'},zh:{dashboard:'总览',trade:'交易',wallet:'资产',deposit:'充值',withdraw:'提现',admin:'后台管理',markets:'市场行情',balance:'资产总估值',volume:'24小时成交额',change:'24小时涨跌',order:'提交订单',buy:'买入',sell:'卖出',amount:'金额（USDT）',price:'参考价格',submit:'提交模拟订单',history:'近期模拟订单',noorders:'暂无订单',demo:'演示模式 — 不涉及真实资金或订单',notice:'真实资金充值、提现及订单执行尚未启用，需先完成合规集成和安全控制。',depositMsg:'充值功能尚未开放，不提供钱包地址。',withdrawMsg:'提现功能尚未开放，不会转移任何资金。',adminMsg:'后台管理仅为界面预览。正式上线需要服务端身份验证及权限控制。',asset:'资产',quantity:'数量',value:'估算价值',status:'系统状态',operational:'前端运行正常',welcome:'清晰掌握数字资产市场',market:'市场',last:'最新价格',action:'操作',view:'查看',pair:'交易对',type:'方向',time:'时间',adminOnly:'演示分析数据，非真实生产数据'}};
const base=[{symbol:'BTC',name:'Bitcoin',price:96342.15,change:2.84,volume:'$28.4B',color:'#f59e0b'},{symbol:'ETH',name:'Ethereum',price:3218.44,change:1.56,volume:'$14.2B',color:'#818cf8'},{symbol:'SOL',name:'Solana',price:142.82,change:-0.78,volume:'$3.1B',color:'#a78bfa'},{symbol:'BNB',name:'BNB',price:614.27,change:0.92,volume:'$1.9B',color:'#eab308'},{symbol:'XRP',name:'XRP',price:2.18,change:-1.12,volume:'$2.6B',color:'#94a3b8'}];
const fmt=(n,d=2)=>Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const COIN_LIMIT = 1000;
const COINS_PER_PAGE = 50;




async function fetch1000Coins() {
  const pageCount = Math.ceil(COIN_LIMIT / 250);
  const allResults = [];

  const sleep = (ms) =>
    new Promise(resolve => setTimeout(resolve, ms));

  for (let page = 1; page <= pageCount; page++) {
    let success = false;
    let retries = 0;

    while (!success && retries < 4) {
      try {
       const response = await fetch(
  `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=${page}&sparkline=false`
);

        if (response.status === 429) {
          throw new Error("RATE_LIMIT");
        }

        if (!response.ok) {
          throw new Error(
            `CoinGecko API error: ${response.status}`
          );
        }

        const data = await response.json();

        allResults.push(...data);
        success = true;

      } catch (error) {
        retries++;

        if (retries >= 4) {
          throw error;
        }

        const delay = error.message === "RATE_LIMIT"
          ? 30000 * Math.pow(2, retries - 1)
          : 10000;

        await sleep(delay);
      }
    }

    if (page < pageCount) {
      await sleep(15000);
    }
  }

  return allResults.slice(0, COIN_LIMIT);
}

  


function App(){const [allCoins, setAllCoins] = useState([]);
const [coinSearch, setCoinSearch] = useState("");
const [coinPage, setCoinPage] = useState(1);
const [coinsLoading, setCoinsLoading] = useState(false);
const [coinsError, setCoinsError] = useState("");const[lang,setLang]=useState('en'),[tab,setTab]=useState('dashboard'),[coin,setCoin]=useState('BTC'),[side,setSide]=useState('buy'),[amount,setAmount]=useState('100'),[orders,setOrders]=useState([]),[prices,setPrices]=useState({}),[period,setPeriod]=useState('1D'),[mobile,setMobile]=useState(false),[error,setError]=useState('');const t=dict[lang];

  const [authMode, setAuthMode] = useState('login');
  const [authToken, setAuthToken] = useState(localStorage.getItem('zencrypto_token') || '');
  const [currentUser, setCurrentUser] = useState(null);
  const [authChecking, setAuthChecking] = useState(Boolean(localStorage.getItem('zencrypto_token')));
  const [authEmail, setAuthEmail] = useState('');
  const [authOpen, setAuthOpen] = useState(false);
  const [authPassword, setAuthPassword] = useState('');
  const [authMessage, setAuthMessage] = useState('');
  const [accountBalances, setAccountBalances] = useState([]);
  const [paperOrders, setPaperOrders] = useState([]);
  const [tradeBusy, setTradeBusy] = useState(false);

  async function apiRequest(path, options={}) {
    const response = await fetch(API_BASE + path, {
      ...options,
      headers: {
        ...(options.headers || {}),
        ...(authToken ? {Authorization:'Bearer '+authToken} : {})
      }
    });
    const data = await response.json().catch(()=>({detail:'Invalid server response'}));
    if (!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }

  async function refreshAccount() {
    if (!authToken) return;
    const [balanceData, orderData] = await Promise.all([
      apiRequest('/account/balances'),
      apiRequest('/trade/orders')
    ]);
    setAccountBalances(balanceData.balances || []);
    setPaperOrders(orderData.orders || []);
    setOrders((orderData.orders || []).map(o=>({
      id:o.id, time:new Date(o.created_at*1000).toLocaleString(),
      coin:o.symbol, side:o.side, amount:Number(o.notional), price:Number(o.price),
      quantity:o.quantity, fee:o.fee, status:o.status
    })));
  }

  useEffect(() => {
    let cancelled=false;
    if (!authToken) {
      setCurrentUser(null); setAccountBalances([]); setPaperOrders([]); setAuthChecking(false);
      return;
    }
    setAuthChecking(true);
    fetch(API_BASE+'/account/me',{headers:{Authorization:'Bearer '+authToken}})
      .then(async response=>{
        const data=await response.json();
        if(!response.ok) throw new Error(data.detail||'Session expired');
        if(!cancelled) setCurrentUser(data);
      })
      .catch(()=>{
        if(!cancelled){localStorage.removeItem('zencrypto_token');setAuthToken('');setCurrentUser(null);}
      })
      .finally(()=>{if(!cancelled)setAuthChecking(false);});
    return ()=>{cancelled=true;};
  },[authToken]);

  useEffect(() => {
    if (!authToken || authChecking) return;
    refreshAccount().catch(error=>setError(error.message));
  },[authToken,authChecking]);

  async function handleAuth(e) {
    e.preventDefault(); setAuthMessage('');
    const endpoint=authMode==='register'?'/auth/register':'/auth/login';
    try {
      const response=await fetch(API_BASE+endpoint,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:authEmail.trim(),password:authPassword})
      });
      const data=await response.json();
      if(!response.ok) throw new Error(data.detail||'Request failed');
      if(authMode==='register'){
        setAuthMessage('Account created. Please log in.');
        setAuthMode('login');setAuthPassword('');
      }else{
        localStorage.setItem('zencrypto_token',data.access_token);
        setAuthToken(data.access_token);setAuthPassword('');
        setAuthOpen(false);setAuthMessage('Login successful.');
      }
    }catch(error){setAuthMessage(error.message||'Unable to connect to API.');}
  }

  async function handleLogout() {
    try { if(authToken) await fetch(API_BASE+'/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+authToken}}); }
    catch(error){console.error('Logout request failed',error);}
    localStorage.removeItem('zencrypto_token');setAuthToken('');setCurrentUser(null);
    setAccountBalances([]);setPaperOrders([]);setOrders([]);
  }

  async function placeOrder(e) {
    e.preventDefault();
    if(!authToken){setAuthOpen(true);setError('Please log in to place a paper order.');return;}
    if(!selected || !selected.price){setError('A current reference price is not available.');return;}
    setTradeBusy(true);setError('');
    try{
      const data=await apiRequest('/trade/orders',{
        method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},
        body:JSON.stringify({symbol:coin,side,quote_amount:amount,reference_price:String(selected.price)})
      });
      setError('Paper order filled: '+data.quantity+' '+data.symbol+' · fee 
useEffect(() => {
  let cancelled = false;

  async function loadCoins() {
    setCoinsLoading(true);
    setCoinsError("");

    try {
      const data = await fetch1000Coins();

      if (!cancelled) {
        setAllCoins(data);
      }
    } catch (error) {
      if (!cancelled) {
        setCoinsError("Failed to load coins");
      }
    } finally {
      if (!cancelled) {
        setCoinsLoading(false);
      }
    }
  }

  loadCoins();

  return () => {
    cancelled = true;
  };
}, []);



useEffect(() => {
  let ws;
  let reconnectTimer;
  let stopped = false;

  function connect() {
    ws = new WebSocket(
  "wss://stream.binance.com:443/ws/!ticker_1d@arr"
);
ws.onopen = () => {
  console.log("[ZenCrypto] Binance connected:", ws.url);
};
    ws.onmessage = (event) => {
      console.log("[ZenCrypto] Message received:", event.data.slice(0, 200));
      try {
        const tickers = JSON.parse(event.data);
        if (!Array.isArray(tickers)) return;

        setPrices((prev) => {
          const next = { ...prev };

          tickers.forEach((t) => {
            if (!t.s || !t.s.endsWith("USDT")) return;

            console.log(
  "[ZenCrypto] Received:",
  tickers.length,
  "USDT pairs:",
  tickers.filter(t => t.s && t.s.endsWith("USDT")).length
);
            const symbol = t.s.slice(0, -4);

            next[symbol] = {
              ...(prev[symbol] || {}),
              usd: Number(t.c),
              usd_24h_change: Number(t.P),
              usd_24h_vol: Number(t.q)
            };
          });

          return next;
        });
      } catch (error) {
        console.error("WebSocket data error:", error);
      }
    };

    ws.onclose = (event) => {
  console.log(
    "[ZenCrypto] WebSocket closed:",
    event.code,
    event.reason
  );

  if (!stopped) {
    reconnectTimer = setTimeout(connect, 5000);
  }
};

ws.onerror = (event) => {
  console.error("[ZenCrypto] WebSocket error:", event);
  ws.close();
};
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    if (ws) ws.close();
  };
}, []);

const filteredCoins = allCoins.filter((coin) => {
  const keyword = coinSearch.toLowerCase().trim();

  return (
    coin.name.toLowerCase().includes(keyword) ||
    coin.symbol.toLowerCase().includes(keyword)
  );
});

const totalCoinPages = Math.max(
  1,
  Math.ceil(filteredCoins.length / COINS_PER_PAGE)
);

const visibleCoins = filteredCoins.slice(
  (coinPage - 1) * COINS_PER_PAGE,
  coinPage * COINS_PER_PAGE
);

               const markets=base.map((x)=>({...x,price:prices[x.symbol]?.usd||x.price,change:prices[x.symbol]?.usd_24h_change??x.change}));
 const selectedCoin = allCoins.find(
  x => x.symbol.toUpperCase() === coin
);

const selected = selectedCoin
  ? {
      ...selectedCoin,
      price: prices[selectedCoin.symbol.toUpperCase()]?.usd
  ?? selectedCoin.current_price ?? 0,

change: prices[selectedCoin.symbol.toUpperCase()]?.usd_24h_change
  ?? selectedCoin.price_change_percentage_24h ?? 0,
      color: "#16c79a"
    }
  : (markets.find(x => x.symbol === coin) || markets[0]);
  
        
    const data=Array.from({length:48},(_,i)=>{const trend=Math.sin(i/5)*.014+Math.cos(i/9)*.009+(i/48-.5)*.026;return {time:`${String(Math.floor(i/2)).padStart(2,'0')}:00`,price:Math.round(selected.price*(1+trend)*100)/100}});
const nav=[['dashboard',LayoutDashboard],['trade',ChartCandlestick],['wallet',Wallet],['deposit',ArrowDownToLine],['withdraw',ArrowUpFromLine],['admin',Shield]];
return <div className="app">{authOpen && (
  <div className="auth-overlay">
    <form className="auth-dialog" onSubmit={handleAuth}>
      <button type="button" className="auth-close" onClick={()=>setAuthOpen(false)}>✕</button>
      <h2>ZenCrypto</h2><h3>{authMode==='login'?'Login':'Register'}</h3>
      <input type="email" placeholder="Email" value={authEmail} onChange={e=>setAuthEmail(e.target.value)} autoComplete="email" required />
      <input type="password" placeholder="Password (12+ characters)" value={authPassword} onChange={e=>setAuthPassword(e.target.value)} minLength={12} maxLength={128} autoComplete={authMode==='login'?'current-password':'new-password'} required />
      <button className="auth-button" type="submit">{authMode==='login'?'Login':'Create account'}</button>
      <p className="auth-switch" onClick={()=>{setAuthMode(authMode==='login'?'register':'login');setAuthMessage('');}}>{authMode==='login'?'Create Account':'Back to Login'}</p>
      <p>{authMessage}</p>
    </form>
  </div>
)}
<aside className={mobile?'sidebar open':'sidebar'}><div className="brand"><div className="brandmark">Z</div><span>Zen<span className="accent">Crypto</span></span></div><div className="navlabel">PLATFORM</div><nav>{nav.map(([key,Icon])=><button key={key} className={tab===key?'nav active':'nav'} onClick={()=>{setTab(key);setMobile(false)}}><Icon size={19}/>{t[key]}</button>)}</nav><div className="sidebarfoot"><span className="dot"/> {t.operational}<p>ZenCrypto v1.0 · Prototype</p></div></aside><main><header><button className="hamburger" onClick={()=>setMobile(!mobile)}><Menu size={23}/></button><div className="headerleft"><span className="breadcrumb">ZenCrypto / {t[tab]}</span><h1>{t[tab]}</h1></div><div className="headeractions"><span className="live"><span className="dot"/> MARKET DATA</span><button className="lang" onClick={()=>setLang(lang==='en'?'zh':'en')}><Globe size={16}/> {lang==='en'?'中文':'English'}</button>{authToken?<><span className="account-email">{authChecking?'Checking…':(currentUser?.email||'Account')}</span><button className="auth-button auth-button-secondary" onClick={handleLogout}>Logout</button></>:<button className="auth-button" onClick={()=>{setAuthMode('login');setAuthOpen(true);}}>Login</button>}<div className="avatar">{currentUser?.email?.[0]?.toUpperCase()||'Z'}</div></div></header><div className="content"><div className="warning"><Shield size={18}/><div><strong>{t.demo}</strong><p>{t.notice}</p></div></div>

{tab==='dashboard'&&<>
  <section className="zc-hero">
    <div className="zc-hero-content">
      <div className="zc-eyebrow">
        ZENCRYPTO DIGITAL ASSET PLATFORM
      </div>
      <h2 className="zc-hero-title">
        Trade Smarter.
        <br/>
        <span>Move Faster.</span>
      </h2>
      <p className="zc-hero-description">
        Explore live cryptocurrency markets and use a fully simulated trading account. No real funds are accepted.
      </p>
      <div className="zc-hero-buttons">
        <button
          className="zc-btn zc-btn-primary"
          onClick={() => document.getElementById('zc-markets')?.scrollIntoView({behavior:'smooth'})}
        >
          Explore Markets
        </button>
        <button
          className="zc-btn zc-btn-secondary"
          onClick={() => setTab('trade')}
        >
          Start Demo Trading
        </button>
      </div>
    </div>
  </section>

  <div className="zc-market-strip">
    {['BTC','ETH','SOL'].map(symbol => {
      const item = prices[symbol];
      const value = item?.usd;
      const change = item?.usd_24h_change;
      return (
        <div className="zc-market-card" key={symbol}>
          <div className="zc-market-symbol">
            {symbol}/USD
          </div>
          <div className="zc-market-price">
            {typeof value === 'number'
              ? '$' + fmt(value)
              : 'Loading...'}
          </div>
          <div className={
            'zc-market-change ' +
            (change >= 0 ? 'zc-positive' : 'zc-negative')
          }>
            {typeof change === 'number'
              ? (change >= 0 ? '+' : '') + change.toFixed(2) + '%'
              : '—'}
          </div>
        </div>
      );
    })}
  </div>

  <h2 className="zc-section-title" id="zc-markets">
    Live Cryptocurrency Markets
  </h2>

  <MarketTable

markets={visibleCoins.map(c => ({
 id: c.id,
symbol: c.symbol.toUpperCase(),
  name: c.name,
  price: prices[c.symbol.toUpperCase()]?.usd ?? c.current_price ?? 0,

change: prices[c.symbol.toUpperCase()]?.usd_24h_change
  ?? c.price_change_percentage_24h ?? 0,

volume: prices[c.symbol.toUpperCase()]?.usd_24h_vol
  ?? c.total_volume ?? 0,
  color: '#16c79a'
}))}
 
t={t}
choose={s=>{setCoin(s);setTab('trade')}}
coinSearch={coinSearch}
setCoinSearch={setCoinSearch}
coinPage={coinPage}
setCoinPage={setCoinPage}
totalCoinPages={totalCoinPages}
coinsLoading={coinsLoading}
coinsError={coinsError}
/></>}

{tab==='trade'&&<><div className="tradehead"><div><div className="coinbadge" style={{background:selected.color}}>{coin[0]}</div><div><h2>{coin}/USDT</h2><span>{selected.name}</span></div></div><div className="pricebig">${fmt(selected.price)} <span className={selected.change>=0?'green':'red'}>{selected.change>=0?'+':''}{fmt(selected.change)}%</span></div></div><div className="tradegrid"><div className="panel chartpanel"><div className="panelhead"><h3>{coin}/USDT <span className="muted">· {t.market}</span></h3><div className="periods">{['1H','1D','1W','1M'].map(x=><button className={period===x?'selected':''} key={x} onClick={()=>setPeriod(x)}>{x}</button>)}</div></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid stroke="#253147" strokeDasharray="3 4" vertical={false}/><XAxis dataKey="time" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} interval={9}/><YAxis domain={['auto','auto']} orientation="right" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} width={78} tickFormatter={v=>'$'+Math.round(v).toLocaleString()}/><Tooltip contentStyle={{background:'#1c273a',border:'1px solid #334155',borderRadius:10}} formatter={v=>['$'+fmt(v),'Price']}/><Line type="monotone" dataKey="price" stroke="#27d6a2" strokeWidth={2.5} dot={false}/></LineChart></ResponsiveContainer></div><p className="fine">Illustrative chart shape; latest reference price may be sourced from CoinGecko. Timeframe buttons are display-only.</p></div><div className="panel orderpanel"><h3>{t.order}</h3><div className="sidebuttons"><button className={side==='buy'?'buy selected':'buy'} onClick={()=>setSide('buy')}>{t.buy}</button><button className={side==='sell'?'sell selected':'sell'} onClick={()=>setSide('sell')}>{t.sell}</button></div><form onSubmit={placeOrder}><label>{t.pair}</label><select
  value={coin}
  onChange={e => setCoin(e.target.value)}
>
  {allCoins.map((x) => (
    <option key={x.id} value={x.symbol.toUpperCase()}>
      {x.symbol}/USDT
    </option>
  ))}
</select><label>{t.price}</label><div className="readonly">${fmt(selected.price)} USD</div><label>{t.amount}</label><input type="number" min="0.01" max="1000000" step="any" value={amount} onChange={e=>setAmount(e.target.value)}/>{error&&<p className="red">{error}</p>}<button className={side==='buy'?'submit buy':'submit sell'} disabled={tradeBusy || authChecking}>{tradeBusy?'Processing…':t.submit}</button></form><p className="fine">PAPER ONLY · No real funds, settlement, custody or external execution.</p></div></div><div className="panel orders"><h3>{t.history}</h3>{orders.length===0?<p className="empty">{t.noorders}</p>:<div className="tablewrap"><table><thead><tr><th>{t.time}</th><th>{t.pair}</th><th>{t.type}</th><th>{t.amount}</th><th>{t.price}</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}><td>{o.time}</td><td>{o.coin}/USDT</td><td className={o.side==='buy'?'green':'red'}>{o.side.toUpperCase()}</td><td>${fmt(o.amount)}</td><td>${fmt(o.price)}</td></tr>)}</tbody></table></div>}</div></>}
{tab==='wallet'&&<><div className="panel stat"><span>Paper trading account · PAPER ONLY</span><strong>Simulated balances</strong><button className="tablebtn" onClick={addPaperFunds}>Add $1,000 test funds</button></div><div className="stats">{accountBalances.map(b=><div className="panel stat" key={b.currency}><span>{b.currency} balance</span><strong>{b.currency==='USD'?'<div className="panel orders"><h3>{t.wallet}</h3><div className="tablewrap"><table><thead><tr><th>{t.asset}</th><th>{t.quantity}</th><th>{t.value}</th></tr></thead><tbody>{accountBalances.filter(b=>b.currency!=='USD').map(b=><tr key={b.currency}><td>{b.currency}</td><td>{fmt(Number(b.amount),8)}</td><td>Market value shown in market page</td></tr>)}</tbody></table></div></div></>}
{(tab==='deposit'||tab==='withdraw')&&<div className="panel disabledpanel"><div className="bigicon">{tab==='deposit'?<ArrowDownToLine size={35}/>:<ArrowUpFromLine size={35}/>}</div><h2>{t[tab]}</h2><p>{tab==='deposit'?t.depositMsg:t.withdrawMsg}</p><div className="noticebox">{t.notice}</div></div>}
{tab==='admin'&&<><div className="stats"><div className="panel stat"><span>Registered users</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Executed orders</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Custodied funds</span><strong>$0.00</strong><small>None</small></div></div><div className="panel disabledpanel"><Shield size={36} color="#27d6a2"/><h2>{t.admin}</h2><p>{t.adminMsg}</p><div className="noticebox">{t.adminOnly}</div></div></>}
<footer>© 2026 ZenCrypto · Prototype only · Not a licensed exchange · No real-money services</footer></div></main></div>}

function MarketTable({
  markets = [],
  t,
  choose,
  coinSearch = "",
  setCoinSearch = () => {},
  coinPage = 1,
  setCoinPage = () => {},
  totalCoinPages = 1,
  coinsLoading = false,
  coinsError = ""
}) {
  return (
    <div className="panel marketpanel">
      <div className="panelhead">
        <h3>{t.markets}</h3>
        <span className="muted">Up to 1000 cryptocurrencies</span>
      </div>

      <div style={{padding: "15px 0"}}>
        <input
          type="text"
          placeholder="Search cryptocurrency name or symbol..."
          value={coinSearch}
          onChange={(e) => {
            setCoinSearch(e.target.value);
            setCoinPage(1);
          }}
          style={{
            width: "100%",
            padding: "12px",
            borderRadius: "8px",
            background: "#101b2d",
            color: "#fff",
            border: "1px solid #334155"
          }}
        />
      </div>

      {coinsLoading && <p>Loading cryptocurrencies...</p>}
      {coinsError && <p className="red">{coinsError}</p>}

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>{t.market}</th>
              <th>{t.last}</th>
              <th>{t.change}</th>
              <th>{t.volume}</th>
              <th>{t.action}</th>
            </tr>
          </thead>

          <tbody>
            {markets.map((x) => (
              <tr key={x.id || x.symbol}>
                <td>
                  <div className="coinrow">
                    <span
                      className="coinsymbol"
                      style={{background: x.color || "#16c79a"}}
                    >
                      {x.symbol[0]}
                    </span>
                    <strong>{x.symbol}</strong>
                    <span className="muted">{x.name}</span>
                  </div>
                </td>

                <td>${fmt(x.price)}</td>

                <td className={x.change >= 0 ? "green" : "red"}>
                  {x.change >= 0 ? "+" : ""}
                  {fmt(x.change)}%
                </td>

                <td>
                  {typeof x.volume === "number"
                    ? fmt(x.volume)
                    : x.volume}
                </td>

                <td>
                  <button
                    className="tablebtn"
                    onClick={() => choose(x.symbol)}
                  >
                    {t.view} →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!coinsLoading && markets.length === 0 && (
        <p>No cryptocurrencies found.</p>
      )}

      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: "15px",
        padding: "20px"
      }}>
        <button
          className="tablebtn"
          disabled={coinPage <= 1}
          onClick={() => setCoinPage(Math.max(1, coinPage - 1))}
        >
          ← Previous
        </button>

        <span>
          Page {coinPage} / {totalCoinPages}
        </span>

        <button
          className="tablebtn"
          disabled={coinPage >= totalCoinPages}
          onClick={() =>
            setCoinPage(Math.min(totalCoinPages, coinPage + 1))
          }
        >
          Next →
        </button>
      </div>

      <p className="fine">
        Market data is for informational purposes.
        Prices may be delayed or unavailable.
        Trading is disabled in demo mode.
      </p>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App/>);
+data.fee);
      await refreshAccount();
    }catch(error){setError(error.message);}
    finally{setTradeBusy(false);}
  }

  async function addPaperFunds() {
    if(!authToken){setAuthOpen(true);return;}
    try{
      await apiRequest('/sandbox/faucet',{
        method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},
        body:JSON.stringify({amount:'1000',currency:'USD'})
      });
      await refreshAccount();
    }catch(error){setError(error.message);}
  }
useEffect(() => {
  let cancelled = false;

  async function loadCoins() {
    setCoinsLoading(true);
    setCoinsError("");

    try {
      const data = await fetch1000Coins();

      if (!cancelled) {
        setAllCoins(data);
      }
    } catch (error) {
      if (!cancelled) {
        setCoinsError("Failed to load coins");
      }
    } finally {
      if (!cancelled) {
        setCoinsLoading(false);
      }
    }
  }

  loadCoins();

  return () => {
    cancelled = true;
  };
}, []);



useEffect(() => {
  let ws;
  let reconnectTimer;
  let stopped = false;

  function connect() {
    ws = new WebSocket(
  "wss://stream.binance.com:443/ws/!ticker_1d@arr"
);
ws.onopen = () => {
  console.log("[ZenCrypto] Binance connected:", ws.url);
};
    ws.onmessage = (event) => {
      console.log("[ZenCrypto] Message received:", event.data.slice(0, 200));
      try {
        const tickers = JSON.parse(event.data);
        if (!Array.isArray(tickers)) return;

        setPrices((prev) => {
          const next = { ...prev };

          tickers.forEach((t) => {
            if (!t.s || !t.s.endsWith("USDT")) return;

            console.log(
  "[ZenCrypto] Received:",
  tickers.length,
  "USDT pairs:",
  tickers.filter(t => t.s && t.s.endsWith("USDT")).length
);
            const symbol = t.s.slice(0, -4);

            next[symbol] = {
              ...(prev[symbol] || {}),
              usd: Number(t.c),
              usd_24h_change: Number(t.P),
              usd_24h_vol: Number(t.q)
            };
          });

          return next;
        });
      } catch (error) {
        console.error("WebSocket data error:", error);
      }
    };

    ws.onclose = (event) => {
  console.log(
    "[ZenCrypto] WebSocket closed:",
    event.code,
    event.reason
  );

  if (!stopped) {
    reconnectTimer = setTimeout(connect, 5000);
  }
};

ws.onerror = (event) => {
  console.error("[ZenCrypto] WebSocket error:", event);
  ws.close();
};
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    if (ws) ws.close();
  };
}, []);

const filteredCoins = allCoins.filter((coin) => {
  const keyword = coinSearch.toLowerCase().trim();

  return (
    coin.name.toLowerCase().includes(keyword) ||
    coin.symbol.toLowerCase().includes(keyword)
  );
});

const totalCoinPages = Math.max(
  1,
  Math.ceil(filteredCoins.length / COINS_PER_PAGE)
);

const visibleCoins = filteredCoins.slice(
  (coinPage - 1) * COINS_PER_PAGE,
  coinPage * COINS_PER_PAGE
);

               const markets=base.map((x)=>({...x,price:prices[x.symbol]?.usd||x.price,change:prices[x.symbol]?.usd_24h_change??x.change}));
 const selectedCoin = allCoins.find(
  x => x.symbol.toUpperCase() === coin
);

const selected = selectedCoin
  ? {
      ...selectedCoin,
      price: prices[selectedCoin.symbol.toUpperCase()]?.usd
  ?? selectedCoin.current_price ?? 0,

change: prices[selectedCoin.symbol.toUpperCase()]?.usd_24h_change
  ?? selectedCoin.price_change_percentage_24h ?? 0,
      color: "#16c79a"
    }
  : (markets.find(x => x.symbol === coin) || markets[0]);
  
        
    const data=Array.from({length:48},(_,i)=>{const trend=Math.sin(i/5)*.014+Math.cos(i/9)*.009+(i/48-.5)*.026;return {time:`${String(Math.floor(i/2)).padStart(2,'0')}:00`,price:Math.round(selected.price*(1+trend)*100)/100}});
const nav=[['dashboard',LayoutDashboard],['trade',ChartCandlestick],['wallet',Wallet],['deposit',ArrowDownToLine],['withdraw',ArrowUpFromLine],['admin',Shield]];
return <div className="app">{!authToken ? (
  <button
    onClick={() => setAuthOpen(true)}
    style={{
      position:'fixed',
      top:'20px',
      right:'80px',
      zIndex:9999,
      padding:'10px 24px',
      background:'#2563eb',
      color:'white',
      border:0,
      borderRadius:'8px',
      cursor:'pointer'
    }}
  >
    Login
  </button>
) : (
  <button
    onClick={() => {
      localStorage.removeItem('zencrypto_token');
      setAuthToken('');
    }}
    style={{position:'fixed',top:'20px',right:'80px',zIndex:9999}}
  >
    Logout
  </button>
)}

{authOpen && (
  <div style={{
    position:'fixed',inset:0,zIndex:10000,
    background:'rgba(0,0,0,0.75)',
    display:'flex',alignItems:'center',
    justifyContent:'center'
  }}>
    <form onSubmit={handleAuth}
      style={{background:'#142238',padding:'30px',
      borderRadius:'16px',width:'340px',color:'white'}}>
      <button type="button"
        onClick={() => setAuthOpen(false)}
        style={{float:'right'}}>✕</button>
      <h2>ZenCrypto</h2>
      <h3>{authMode === 'login' ? 'Login' : 'Register'}</h3>
      <input type="email" placeholder="Email"
        value={authEmail}
        onChange={e=>setAuthEmail(e.target.value)}
        required />
      <input type="password" placeholder="Password"
        value={authPassword}
        onChange={e=>setAuthPassword(e.target.value)}
        required />
      <button type="submit">
        {authMode === 'login' ? 'Login' : 'Register'}
      </button>
      <p onClick={() => setAuthMode(
        authMode === 'login' ? 'register' : 'login'
      )}>
        {authMode === 'login' ? 'Create Account' : 'Back to Login'}
      </p>
      <p>{authMessage}</p>
    </form>
  </div>
)}<aside className={mobile?'sidebar open':'sidebar'}><div className="brand"><div className="brandmark">Z</div><span>Zen<span className="accent">Crypto</span></span></div><div className="navlabel">PLATFORM</div><nav>{nav.map(([key,Icon])=><button key={key} className={tab===key?'nav active':'nav'} onClick={()=>{setTab(key);setMobile(false)}}><Icon size={19}/>{t[key]}</button>)}</nav><div className="sidebarfoot"><span className="dot"/> {t.operational}<p>ZenCrypto v1.0 · Prototype</p></div></aside><main><header><button className="hamburger" onClick={()=>setMobile(!mobile)}><Menu size={23}/></button><div className="headerleft"><span className="breadcrumb">ZenCrypto / {t[tab]}</span><h1>{t[tab]}</h1></div><div className="headeractions"><span className="live"><span className="dot"/> MARKET DATA</span><button className="lang" onClick={()=>setLang(lang==='en'?'zh':'en')}><Globe size={16}/> {lang==='en'?'中文':'English'}</button><div className="avatar">Z</div></div></header><div className="content"><div className="warning"><Shield size={18}/><div><strong>{t.demo}</strong><p>{t.notice}</p></div></div>

{tab==='dashboard'&&<>
  <section className="zc-hero">
    <div className="zc-hero-content">
      <div className="zc-eyebrow">
        ZENCRYPTO DIGITAL ASSET PLATFORM
      </div>
      <h2 className="zc-hero-title">
        Trade Smarter.
        <br/>
        <span>Move Faster.</span>
      </h2>
      <p className="zc-hero-description">
        Explore live cryptocurrency markets, follow market
        movements, and experience modern demo trading.
      </p>
      <div className="zc-hero-buttons">
        <button
          className="zc-btn zc-btn-primary"
          onClick={() => document.getElementById('zc-markets')?.scrollIntoView({behavior:'smooth'})}
        >
          Explore Markets
        </button>
        <button
          className="zc-btn zc-btn-secondary"
          onClick={() => setTab('trade')}
        >
          Start Demo Trading
        </button>
      </div>
    </div>
  </section>

  <div className="zc-market-strip">
    {['BTC','ETH','SOL'].map(symbol => {
      const item = prices[symbol];
      const value = item?.usd;
      const change = item?.usd_24h_change;
      return (
        <div className="zc-market-card" key={symbol}>
          <div className="zc-market-symbol">
            {symbol}/USD
          </div>
          <div className="zc-market-price">
            {typeof value === 'number'
              ? '$' + fmt(value)
              : 'Loading...'}
          </div>
          <div className={
            'zc-market-change ' +
            (change >= 0 ? 'zc-positive' : 'zc-negative')
          }>
            {typeof change === 'number'
              ? (change >= 0 ? '+' : '') + change.toFixed(2) + '%'
              : '—'}
          </div>
        </div>
      );
    })}
  </div>

  <h2 className="zc-section-title" id="zc-markets">
    Live Cryptocurrency Markets
  </h2>

  <MarketTable

markets={visibleCoins.map(c => ({
 id: c.id,
symbol: c.symbol.toUpperCase(),
  name: c.name,
  price: prices[c.symbol.toUpperCase()]?.usd ?? c.current_price ?? 0,

change: prices[c.symbol.toUpperCase()]?.usd_24h_change
  ?? c.price_change_percentage_24h ?? 0,

volume: prices[c.symbol.toUpperCase()]?.usd_24h_vol
  ?? c.total_volume ?? 0,
  color: '#16c79a'
}))}
 
t={t}
choose={s=>{setCoin(s);setTab('trade')}}
coinSearch={coinSearch}
setCoinSearch={setCoinSearch}
coinPage={coinPage}
setCoinPage={setCoinPage}
totalCoinPages={totalCoinPages}
coinsLoading={coinsLoading}
coinsError={coinsError}
/></>}

{tab==='trade'&&<><div className="tradehead"><div><div className="coinbadge" style={{background:selected.color}}>{coin[0]}</div><div><h2>{coin}/USDT</h2><span>{selected.name}</span></div></div><div className="pricebig">${fmt(selected.price)} <span className={selected.change>=0?'green':'red'}>{selected.change>=0?'+':''}{fmt(selected.change)}%</span></div></div><div className="tradegrid"><div className="panel chartpanel"><div className="panelhead"><h3>{coin}/USDT <span className="muted">· {t.market}</span></h3><div className="periods">{['1H','1D','1W','1M'].map(x=><button className={period===x?'selected':''} key={x} onClick={()=>setPeriod(x)}>{x}</button>)}</div></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid stroke="#253147" strokeDasharray="3 4" vertical={false}/><XAxis dataKey="time" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} interval={9}/><YAxis domain={['auto','auto']} orientation="right" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} width={78} tickFormatter={v=>'$'+Math.round(v).toLocaleString()}/><Tooltip contentStyle={{background:'#1c273a',border:'1px solid #334155',borderRadius:10}} formatter={v=>['$'+fmt(v),'Price']}/><Line type="monotone" dataKey="price" stroke="#27d6a2" strokeWidth={2.5} dot={false}/></LineChart></ResponsiveContainer></div><p className="fine">Illustrative chart shape; latest reference price may be sourced from CoinGecko. Timeframe buttons are display-only.</p></div><div className="panel orderpanel"><h3>{t.order}</h3><div className="sidebuttons"><button className={side==='buy'?'buy selected':'buy'} onClick={()=>setSide('buy')}>{t.buy}</button><button className={side==='sell'?'sell selected':'sell'} onClick={()=>setSide('sell')}>{t.sell}</button></div><form onSubmit={placeOrder}><label>{t.pair}</label><select
  value={coin}
  onChange={e => setCoin(e.target.value)}
>
  {allCoins.map((x) => (
    <option key={x.id} value={x.symbol.toUpperCase()}>
      {x.symbol}/USDT
    </option>
  ))}
</select><label>{t.price}</label><div className="readonly">${fmt(selected.price)} USD</div><label>{t.amount}</label><input type="number" min="0.01" max="1000000" step="any" value={amount} onChange={e=>setAmount(e.target.value)}/>{error&&<p className="red">{error}</p>}<button className={side==='buy'?'submit buy':'submit sell'}>{t.submit}</button></form><p className="fine">No execution, settlement, or custody occurs.</p></div></div><div className="panel orders"><h3>{t.history}</h3>{orders.length===0?<p className="empty">{t.noorders}</p>:<div className="tablewrap"><table><thead><tr><th>{t.time}</th><th>{t.pair}</th><th>{t.type}</th><th>{t.amount}</th><th>{t.price}</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}><td>{o.time}</td><td>{o.coin}/USDT</td><td className={o.side==='buy'?'green':'red'}>{o.side.toUpperCase()}</td><td>${fmt(o.amount)}</td><td>${fmt(o.price)}</td></tr>)}</tbody></table></div>}</div></>}
{tab==='wallet'&&<><div className="stats"><div className="panel stat"><span>{t.balance}</span><strong>$0.00</strong><small>0.00 USDT</small></div><div className="panel stat"><span>Available</span><strong>$0.00</strong></div><div className="panel stat"><span>In orders</span><strong>$0.00</strong></div></div><div className="panel orders"><h3>{t.wallet}</h3><div className="tablewrap"><table><thead><tr><th>{t.asset}</th><th>{t.quantity}</th><th>{t.value}</th></tr></thead><tbody>{markets.map(x=><tr key={x.symbol}><td>{x.symbol} · {x.name}</td><td>0.00000000</td><td>$0.00</td></tr>)}</tbody></table></div></div></>}
{(tab==='deposit'||tab==='withdraw')&&<div className="panel disabledpanel"><div className="bigicon">{tab==='deposit'?<ArrowDownToLine size={35}/>:<ArrowUpFromLine size={35}/>}</div><h2>{t[tab]}</h2><p>{tab==='deposit'?t.depositMsg:t.withdrawMsg}</p><div className="noticebox">{t.notice}</div></div>}
{tab==='admin'&&<><div className="stats"><div className="panel stat"><span>Registered users</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Executed orders</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Custodied funds</span><strong>$0.00</strong><small>None</small></div></div><div className="panel disabledpanel"><Shield size={36} color="#27d6a2"/><h2>{t.admin}</h2><p>{t.adminMsg}</p><div className="noticebox">{t.adminOnly}</div></div></>}
<footer>© 2026 ZenCrypto · Prototype only · Not a licensed exchange · No real-money services</footer></div></main></div>}

function MarketTable({
  markets = [],
  t,
  choose,
  coinSearch = "",
  setCoinSearch = () => {},
  coinPage = 1,
  setCoinPage = () => {},
  totalCoinPages = 1,
  coinsLoading = false,
  coinsError = ""
}) {
  return (
    <div className="panel marketpanel">
      <div className="panelhead">
        <h3>{t.markets}</h3>
        <span className="muted">Up to 1000 cryptocurrencies</span>
      </div>

      <div style={{padding: "15px 0"}}>
        <input
          type="text"
          placeholder="Search cryptocurrency name or symbol..."
          value={coinSearch}
          onChange={(e) => {
            setCoinSearch(e.target.value);
            setCoinPage(1);
          }}
          style={{
            width: "100%",
            padding: "12px",
            borderRadius: "8px",
            background: "#101b2d",
            color: "#fff",
            border: "1px solid #334155"
          }}
        />
      </div>

      {coinsLoading && <p>Loading cryptocurrencies...</p>}
      {coinsError && <p className="red">{coinsError}</p>}

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>{t.market}</th>
              <th>{t.last}</th>
              <th>{t.change}</th>
              <th>{t.volume}</th>
              <th>{t.action}</th>
            </tr>
          </thead>

          <tbody>
            {markets.map((x) => (
              <tr key={x.id || x.symbol}>
                <td>
                  <div className="coinrow">
                    <span
                      className="coinsymbol"
                      style={{background: x.color || "#16c79a"}}
                    >
                      {x.symbol[0]}
                    </span>
                    <strong>{x.symbol}</strong>
                    <span className="muted">{x.name}</span>
                  </div>
                </td>

                <td>${fmt(x.price)}</td>

                <td className={x.change >= 0 ? "green" : "red"}>
                  {x.change >= 0 ? "+" : ""}
                  {fmt(x.change)}%
                </td>

                <td>
                  {typeof x.volume === "number"
                    ? fmt(x.volume)
                    : x.volume}
                </td>

                <td>
                  <button
                    className="tablebtn"
                    onClick={() => choose(x.symbol)}
                  >
                    {t.view} →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!coinsLoading && markets.length === 0 && (
        <p>No cryptocurrencies found.</p>
      )}

      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: "15px",
        padding: "20px"
      }}>
        <button
          className="tablebtn"
          disabled={coinPage <= 1}
          onClick={() => setCoinPage(Math.max(1, coinPage - 1))}
        >
          ← Previous
        </button>

        <span>
          Page {coinPage} / {totalCoinPages}
        </span>

        <button
          className="tablebtn"
          disabled={coinPage >= totalCoinPages}
          onClick={() =>
            setCoinPage(Math.min(totalCoinPages, coinPage + 1))
          }
        >
          Next →
        </button>
      </div>

      <p className="fine">
        Market data is for informational purposes.
        Prices may be delayed or unavailable.
        Trading is disabled in demo mode.
      </p>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App/>);
:''}{fmt(Number(b.amount),b.currency==='USD'?2:8)}</strong></div>)}</div><div className="panel orders"><h3>{t.wallet}</h3><div className="tablewrap"><table><thead><tr><th>{t.asset}</th><th>{t.quantity}</th><th>{t.value}</th></tr></thead><tbody>{markets.map(x=><tr key={x.symbol}><td>{x.symbol} · {x.name}</td><td>0.00000000</td><td>$0.00</td></tr>)}</tbody></table></div></div></>}
{(tab==='deposit'||tab==='withdraw')&&<div className="panel disabledpanel"><div className="bigicon">{tab==='deposit'?<ArrowDownToLine size={35}/>:<ArrowUpFromLine size={35}/>}</div><h2>{t[tab]}</h2><p>{tab==='deposit'?t.depositMsg:t.withdrawMsg}</p><div className="noticebox">{t.notice}</div></div>}
{tab==='admin'&&<><div className="stats"><div className="panel stat"><span>Registered users</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Executed orders</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Custodied funds</span><strong>$0.00</strong><small>None</small></div></div><div className="panel disabledpanel"><Shield size={36} color="#27d6a2"/><h2>{t.admin}</h2><p>{t.adminMsg}</p><div className="noticebox">{t.adminOnly}</div></div></>}
<footer>© 2026 ZenCrypto · Prototype only · Not a licensed exchange · No real-money services</footer></div></main></div>}

function MarketTable({
  markets = [],
  t,
  choose,
  coinSearch = "",
  setCoinSearch = () => {},
  coinPage = 1,
  setCoinPage = () => {},
  totalCoinPages = 1,
  coinsLoading = false,
  coinsError = ""
}) {
  return (
    <div className="panel marketpanel">
      <div className="panelhead">
        <h3>{t.markets}</h3>
        <span className="muted">Up to 1000 cryptocurrencies</span>
      </div>

      <div style={{padding: "15px 0"}}>
        <input
          type="text"
          placeholder="Search cryptocurrency name or symbol..."
          value={coinSearch}
          onChange={(e) => {
            setCoinSearch(e.target.value);
            setCoinPage(1);
          }}
          style={{
            width: "100%",
            padding: "12px",
            borderRadius: "8px",
            background: "#101b2d",
            color: "#fff",
            border: "1px solid #334155"
          }}
        />
      </div>

      {coinsLoading && <p>Loading cryptocurrencies...</p>}
      {coinsError && <p className="red">{coinsError}</p>}

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>{t.market}</th>
              <th>{t.last}</th>
              <th>{t.change}</th>
              <th>{t.volume}</th>
              <th>{t.action}</th>
            </tr>
          </thead>

          <tbody>
            {markets.map((x) => (
              <tr key={x.id || x.symbol}>
                <td>
                  <div className="coinrow">
                    <span
                      className="coinsymbol"
                      style={{background: x.color || "#16c79a"}}
                    >
                      {x.symbol[0]}
                    </span>
                    <strong>{x.symbol}</strong>
                    <span className="muted">{x.name}</span>
                  </div>
                </td>

                <td>${fmt(x.price)}</td>

                <td className={x.change >= 0 ? "green" : "red"}>
                  {x.change >= 0 ? "+" : ""}
                  {fmt(x.change)}%
                </td>

                <td>
                  {typeof x.volume === "number"
                    ? fmt(x.volume)
                    : x.volume}
                </td>

                <td>
                  <button
                    className="tablebtn"
                    onClick={() => choose(x.symbol)}
                  >
                    {t.view} →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!coinsLoading && markets.length === 0 && (
        <p>No cryptocurrencies found.</p>
      )}

      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: "15px",
        padding: "20px"
      }}>
        <button
          className="tablebtn"
          disabled={coinPage <= 1}
          onClick={() => setCoinPage(Math.max(1, coinPage - 1))}
        >
          ← Previous
        </button>

        <span>
          Page {coinPage} / {totalCoinPages}
        </span>

        <button
          className="tablebtn"
          disabled={coinPage >= totalCoinPages}
          onClick={() =>
            setCoinPage(Math.min(totalCoinPages, coinPage + 1))
          }
        >
          Next →
        </button>
      </div>

      <p className="fine">
        Market data is for informational purposes.
        Prices may be delayed or unavailable.
        Trading is disabled in demo mode.
      </p>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App/>);
+data.fee);
      await refreshAccount();
    }catch(error){setError(error.message);}
    finally{setTradeBusy(false);}
  }

  async function addPaperFunds() {
    if(!authToken){setAuthOpen(true);return;}
    try{
      await apiRequest('/sandbox/faucet',{
        method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},
        body:JSON.stringify({amount:'1000',currency:'USD'})
      });
      await refreshAccount();
    }catch(error){setError(error.message);}
  }
useEffect(() => {
  let cancelled = false;

  async function loadCoins() {
    setCoinsLoading(true);
    setCoinsError("");

    try {
      const data = await fetch1000Coins();

      if (!cancelled) {
        setAllCoins(data);
      }
    } catch (error) {
      if (!cancelled) {
        setCoinsError("Failed to load coins");
      }
    } finally {
      if (!cancelled) {
        setCoinsLoading(false);
      }
    }
  }

  loadCoins();

  return () => {
    cancelled = true;
  };
}, []);



useEffect(() => {
  let ws;
  let reconnectTimer;
  let stopped = false;

  function connect() {
    ws = new WebSocket(
  "wss://stream.binance.com:443/ws/!ticker_1d@arr"
);
ws.onopen = () => {
  console.log("[ZenCrypto] Binance connected:", ws.url);
};
    ws.onmessage = (event) => {
      console.log("[ZenCrypto] Message received:", event.data.slice(0, 200));
      try {
        const tickers = JSON.parse(event.data);
        if (!Array.isArray(tickers)) return;

        setPrices((prev) => {
          const next = { ...prev };

          tickers.forEach((t) => {
            if (!t.s || !t.s.endsWith("USDT")) return;

            console.log(
  "[ZenCrypto] Received:",
  tickers.length,
  "USDT pairs:",
  tickers.filter(t => t.s && t.s.endsWith("USDT")).length
);
            const symbol = t.s.slice(0, -4);

            next[symbol] = {
              ...(prev[symbol] || {}),
              usd: Number(t.c),
              usd_24h_change: Number(t.P),
              usd_24h_vol: Number(t.q)
            };
          });

          return next;
        });
      } catch (error) {
        console.error("WebSocket data error:", error);
      }
    };

    ws.onclose = (event) => {
  console.log(
    "[ZenCrypto] WebSocket closed:",
    event.code,
    event.reason
  );

  if (!stopped) {
    reconnectTimer = setTimeout(connect, 5000);
  }
};

ws.onerror = (event) => {
  console.error("[ZenCrypto] WebSocket error:", event);
  ws.close();
};
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    if (ws) ws.close();
  };
}, []);

const filteredCoins = allCoins.filter((coin) => {
  const keyword = coinSearch.toLowerCase().trim();

  return (
    coin.name.toLowerCase().includes(keyword) ||
    coin.symbol.toLowerCase().includes(keyword)
  );
});

const totalCoinPages = Math.max(
  1,
  Math.ceil(filteredCoins.length / COINS_PER_PAGE)
);

const visibleCoins = filteredCoins.slice(
  (coinPage - 1) * COINS_PER_PAGE,
  coinPage * COINS_PER_PAGE
);

               const markets=base.map((x)=>({...x,price:prices[x.symbol]?.usd||x.price,change:prices[x.symbol]?.usd_24h_change??x.change}));
 const selectedCoin = allCoins.find(
  x => x.symbol.toUpperCase() === coin
);

const selected = selectedCoin
  ? {
      ...selectedCoin,
      price: prices[selectedCoin.symbol.toUpperCase()]?.usd
  ?? selectedCoin.current_price ?? 0,

change: prices[selectedCoin.symbol.toUpperCase()]?.usd_24h_change
  ?? selectedCoin.price_change_percentage_24h ?? 0,
      color: "#16c79a"
    }
  : (markets.find(x => x.symbol === coin) || markets[0]);
  
        
    const data=Array.from({length:48},(_,i)=>{const trend=Math.sin(i/5)*.014+Math.cos(i/9)*.009+(i/48-.5)*.026;return {time:`${String(Math.floor(i/2)).padStart(2,'0')}:00`,price:Math.round(selected.price*(1+trend)*100)/100}});
const nav=[['dashboard',LayoutDashboard],['trade',ChartCandlestick],['wallet',Wallet],['deposit',ArrowDownToLine],['withdraw',ArrowUpFromLine],['admin',Shield]];
return <div className="app">{!authToken ? (
  <button
    onClick={() => setAuthOpen(true)}
    style={{
      position:'fixed',
      top:'20px',
      right:'80px',
      zIndex:9999,
      padding:'10px 24px',
      background:'#2563eb',
      color:'white',
      border:0,
      borderRadius:'8px',
      cursor:'pointer'
    }}
  >
    Login
  </button>
) : (
  <button
    onClick={() => {
      localStorage.removeItem('zencrypto_token');
      setAuthToken('');
    }}
    style={{position:'fixed',top:'20px',right:'80px',zIndex:9999}}
  >
    Logout
  </button>
)}

{authOpen && (
  <div style={{
    position:'fixed',inset:0,zIndex:10000,
    background:'rgba(0,0,0,0.75)',
    display:'flex',alignItems:'center',
    justifyContent:'center'
  }}>
    <form onSubmit={handleAuth}
      style={{background:'#142238',padding:'30px',
      borderRadius:'16px',width:'340px',color:'white'}}>
      <button type="button"
        onClick={() => setAuthOpen(false)}
        style={{float:'right'}}>✕</button>
      <h2>ZenCrypto</h2>
      <h3>{authMode === 'login' ? 'Login' : 'Register'}</h3>
      <input type="email" placeholder="Email"
        value={authEmail}
        onChange={e=>setAuthEmail(e.target.value)}
        required />
      <input type="password" placeholder="Password"
        value={authPassword}
        onChange={e=>setAuthPassword(e.target.value)}
        required />
      <button type="submit">
        {authMode === 'login' ? 'Login' : 'Register'}
      </button>
      <p onClick={() => setAuthMode(
        authMode === 'login' ? 'register' : 'login'
      )}>
        {authMode === 'login' ? 'Create Account' : 'Back to Login'}
      </p>
      <p>{authMessage}</p>
    </form>
  </div>
)}<aside className={mobile?'sidebar open':'sidebar'}><div className="brand"><div className="brandmark">Z</div><span>Zen<span className="accent">Crypto</span></span></div><div className="navlabel">PLATFORM</div><nav>{nav.map(([key,Icon])=><button key={key} className={tab===key?'nav active':'nav'} onClick={()=>{setTab(key);setMobile(false)}}><Icon size={19}/>{t[key]}</button>)}</nav><div className="sidebarfoot"><span className="dot"/> {t.operational}<p>ZenCrypto v1.0 · Prototype</p></div></aside><main><header><button className="hamburger" onClick={()=>setMobile(!mobile)}><Menu size={23}/></button><div className="headerleft"><span className="breadcrumb">ZenCrypto / {t[tab]}</span><h1>{t[tab]}</h1></div><div className="headeractions"><span className="live"><span className="dot"/> MARKET DATA</span><button className="lang" onClick={()=>setLang(lang==='en'?'zh':'en')}><Globe size={16}/> {lang==='en'?'中文':'English'}</button><div className="avatar">Z</div></div></header><div className="content"><div className="warning"><Shield size={18}/><div><strong>{t.demo}</strong><p>{t.notice}</p></div></div>

{tab==='dashboard'&&<>
  <section className="zc-hero">
    <div className="zc-hero-content">
      <div className="zc-eyebrow">
        ZENCRYPTO DIGITAL ASSET PLATFORM
      </div>
      <h2 className="zc-hero-title">
        Trade Smarter.
        <br/>
        <span>Move Faster.</span>
      </h2>
      <p className="zc-hero-description">
        Explore live cryptocurrency markets, follow market
        movements, and experience modern demo trading.
      </p>
      <div className="zc-hero-buttons">
        <button
          className="zc-btn zc-btn-primary"
          onClick={() => document.getElementById('zc-markets')?.scrollIntoView({behavior:'smooth'})}
        >
          Explore Markets
        </button>
        <button
          className="zc-btn zc-btn-secondary"
          onClick={() => setTab('trade')}
        >
          Start Demo Trading
        </button>
      </div>
    </div>
  </section>

  <div className="zc-market-strip">
    {['BTC','ETH','SOL'].map(symbol => {
      const item = prices[symbol];
      const value = item?.usd;
      const change = item?.usd_24h_change;
      return (
        <div className="zc-market-card" key={symbol}>
          <div className="zc-market-symbol">
            {symbol}/USD
          </div>
          <div className="zc-market-price">
            {typeof value === 'number'
              ? '$' + fmt(value)
              : 'Loading...'}
          </div>
          <div className={
            'zc-market-change ' +
            (change >= 0 ? 'zc-positive' : 'zc-negative')
          }>
            {typeof change === 'number'
              ? (change >= 0 ? '+' : '') + change.toFixed(2) + '%'
              : '—'}
          </div>
        </div>
      );
    })}
  </div>

  <h2 className="zc-section-title" id="zc-markets">
    Live Cryptocurrency Markets
  </h2>

  <MarketTable

markets={visibleCoins.map(c => ({
 id: c.id,
symbol: c.symbol.toUpperCase(),
  name: c.name,
  price: prices[c.symbol.toUpperCase()]?.usd ?? c.current_price ?? 0,

change: prices[c.symbol.toUpperCase()]?.usd_24h_change
  ?? c.price_change_percentage_24h ?? 0,

volume: prices[c.symbol.toUpperCase()]?.usd_24h_vol
  ?? c.total_volume ?? 0,
  color: '#16c79a'
}))}
 
t={t}
choose={s=>{setCoin(s);setTab('trade')}}
coinSearch={coinSearch}
setCoinSearch={setCoinSearch}
coinPage={coinPage}
setCoinPage={setCoinPage}
totalCoinPages={totalCoinPages}
coinsLoading={coinsLoading}
coinsError={coinsError}
/></>}

{tab==='trade'&&<><div className="tradehead"><div><div className="coinbadge" style={{background:selected.color}}>{coin[0]}</div><div><h2>{coin}/USDT</h2><span>{selected.name}</span></div></div><div className="pricebig">${fmt(selected.price)} <span className={selected.change>=0?'green':'red'}>{selected.change>=0?'+':''}{fmt(selected.change)}%</span></div></div><div className="tradegrid"><div className="panel chartpanel"><div className="panelhead"><h3>{coin}/USDT <span className="muted">· {t.market}</span></h3><div className="periods">{['1H','1D','1W','1M'].map(x=><button className={period===x?'selected':''} key={x} onClick={()=>setPeriod(x)}>{x}</button>)}</div></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid stroke="#253147" strokeDasharray="3 4" vertical={false}/><XAxis dataKey="time" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} interval={9}/><YAxis domain={['auto','auto']} orientation="right" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} width={78} tickFormatter={v=>'$'+Math.round(v).toLocaleString()}/><Tooltip contentStyle={{background:'#1c273a',border:'1px solid #334155',borderRadius:10}} formatter={v=>['$'+fmt(v),'Price']}/><Line type="monotone" dataKey="price" stroke="#27d6a2" strokeWidth={2.5} dot={false}/></LineChart></ResponsiveContainer></div><p className="fine">Illustrative chart shape; latest reference price may be sourced from CoinGecko. Timeframe buttons are display-only.</p></div><div className="panel orderpanel"><h3>{t.order}</h3><div className="sidebuttons"><button className={side==='buy'?'buy selected':'buy'} onClick={()=>setSide('buy')}>{t.buy}</button><button className={side==='sell'?'sell selected':'sell'} onClick={()=>setSide('sell')}>{t.sell}</button></div><form onSubmit={placeOrder}><label>{t.pair}</label><select
  value={coin}
  onChange={e => setCoin(e.target.value)}
>
  {allCoins.map((x) => (
    <option key={x.id} value={x.symbol.toUpperCase()}>
      {x.symbol}/USDT
    </option>
  ))}
</select><label>{t.price}</label><div className="readonly">${fmt(selected.price)} USD</div><label>{t.amount}</label><input type="number" min="0.01" max="1000000" step="any" value={amount} onChange={e=>setAmount(e.target.value)}/>{error&&<p className="red">{error}</p>}<button className={side==='buy'?'submit buy':'submit sell'}>{t.submit}</button></form><p className="fine">No execution, settlement, or custody occurs.</p></div></div><div className="panel orders"><h3>{t.history}</h3>{orders.length===0?<p className="empty">{t.noorders}</p>:<div className="tablewrap"><table><thead><tr><th>{t.time}</th><th>{t.pair}</th><th>{t.type}</th><th>{t.amount}</th><th>{t.price}</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}><td>{o.time}</td><td>{o.coin}/USDT</td><td className={o.side==='buy'?'green':'red'}>{o.side.toUpperCase()}</td><td>${fmt(o.amount)}</td><td>${fmt(o.price)}</td></tr>)}</tbody></table></div>}</div></>}
{tab==='wallet'&&<><div className="stats"><div className="panel stat"><span>{t.balance}</span><strong>$0.00</strong><small>0.00 USDT</small></div><div className="panel stat"><span>Available</span><strong>$0.00</strong></div><div className="panel stat"><span>In orders</span><strong>$0.00</strong></div></div><div className="panel orders"><h3>{t.wallet}</h3><div className="tablewrap"><table><thead><tr><th>{t.asset}</th><th>{t.quantity}</th><th>{t.value}</th></tr></thead><tbody>{markets.map(x=><tr key={x.symbol}><td>{x.symbol} · {x.name}</td><td>0.00000000</td><td>$0.00</td></tr>)}</tbody></table></div></div></>}
{(tab==='deposit'||tab==='withdraw')&&<div className="panel disabledpanel"><div className="bigicon">{tab==='deposit'?<ArrowDownToLine size={35}/>:<ArrowUpFromLine size={35}/>}</div><h2>{t[tab]}</h2><p>{tab==='deposit'?t.depositMsg:t.withdrawMsg}</p><div className="noticebox">{t.notice}</div></div>}
{tab==='admin'&&<><div className="stats"><div className="panel stat"><span>Registered users</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Executed orders</span><strong>0</strong><small>Demo only</small></div><div className="panel stat"><span>Custodied funds</span><strong>$0.00</strong><small>None</small></div></div><div className="panel disabledpanel"><Shield size={36} color="#27d6a2"/><h2>{t.admin}</h2><p>{t.adminMsg}</p><div className="noticebox">{t.adminOnly}</div></div></>}
<footer>© 2026 ZenCrypto · Prototype only · Not a licensed exchange · No real-money services</footer></div></main></div>}

function MarketTable({
  markets = [],
  t,
  choose,
  coinSearch = "",
  setCoinSearch = () => {},
  coinPage = 1,
  setCoinPage = () => {},
  totalCoinPages = 1,
  coinsLoading = false,
  coinsError = ""
}) {
  return (
    <div className="panel marketpanel">
      <div className="panelhead">
        <h3>{t.markets}</h3>
        <span className="muted">Up to 1000 cryptocurrencies</span>
      </div>

      <div style={{padding: "15px 0"}}>
        <input
          type="text"
          placeholder="Search cryptocurrency name or symbol..."
          value={coinSearch}
          onChange={(e) => {
            setCoinSearch(e.target.value);
            setCoinPage(1);
          }}
          style={{
            width: "100%",
            padding: "12px",
            borderRadius: "8px",
            background: "#101b2d",
            color: "#fff",
            border: "1px solid #334155"
          }}
        />
      </div>

      {coinsLoading && <p>Loading cryptocurrencies...</p>}
      {coinsError && <p className="red">{coinsError}</p>}

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>{t.market}</th>
              <th>{t.last}</th>
              <th>{t.change}</th>
              <th>{t.volume}</th>
              <th>{t.action}</th>
            </tr>
          </thead>

          <tbody>
            {markets.map((x) => (
              <tr key={x.id || x.symbol}>
                <td>
                  <div className="coinrow">
                    <span
                      className="coinsymbol"
                      style={{background: x.color || "#16c79a"}}
                    >
                      {x.symbol[0]}
                    </span>
                    <strong>{x.symbol}</strong>
                    <span className="muted">{x.name}</span>
                  </div>
                </td>

                <td>${fmt(x.price)}</td>

                <td className={x.change >= 0 ? "green" : "red"}>
                  {x.change >= 0 ? "+" : ""}
                  {fmt(x.change)}%
                </td>

                <td>
                  {typeof x.volume === "number"
                    ? fmt(x.volume)
                    : x.volume}
                </td>

                <td>
                  <button
                    className="tablebtn"
                    onClick={() => choose(x.symbol)}
                  >
                    {t.view} →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!coinsLoading && markets.length === 0 && (
        <p>No cryptocurrencies found.</p>
      )}

      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: "15px",
        padding: "20px"
      }}>
        <button
          className="tablebtn"
          disabled={coinPage <= 1}
          onClick={() => setCoinPage(Math.max(1, coinPage - 1))}
        >
          ← Previous
        </button>

        <span>
          Page {coinPage} / {totalCoinPages}
        </span>

        <button
          className="tablebtn"
          disabled={coinPage >= totalCoinPages}
          onClick={() =>
            setCoinPage(Math.min(totalCoinPages, coinPage + 1))
          }
        >
          Next →
        </button>
      </div>

      <p className="fine">
        Market data is for informational purposes.
        Prices may be delayed or unavailable.
        Trading is disabled in demo mode.
      </p>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App/>);
