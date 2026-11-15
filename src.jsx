import React,{useState,useEffect} from 'react';import{createRoot}from'react-dom/client';import{LineChart,Line,XAxis,YAxis,Tooltip,ResponsiveContainer,CartesianGrid}from'recharts';import{LayoutDashboard,ChartCandlestick,Wallet,ArrowDownToLine,ArrowUpFromLine,Shield,Globe,Menu,Activity,Search,LogOut}from'lucide-react';import './style.css';
const dict={en:{dashboard:'Overview',trade:'Trade',wallet:'Assets',deposit:'Deposit',withdraw:'Withdraw',admin:'Admin',markets:'Markets',balance:'Total portfolio value',volume:'24h Volume',change:'24h Change',order:'Place order',buy:'Buy',sell:'Sell',amount:'Amount (USDT)',price:'Reference price',submit:'Submit simulated order',history:'Recent simulated orders',noorders:'No orders yet',demo:'DEMO MODE — No real funds or orders',notice:'Real-money deposits, withdrawals and order execution are disabled until licensed integrations and security controls are complete.',depositMsg:'Deposits are disabled. No wallet addresses are issued.',withdrawMsg:'Withdrawals are disabled. No funds can be transferred.',adminMsg:'Admin dashboard preview only. Production access requires server-side authentication and role controls.',asset:'Asset',quantity:'Quantity',value:'Estimated value',status:'System status',operational:'Frontend operational',welcome:'Digital asset markets, clearly presented',market:'Market',last:'Last price',action:'Action',view:'View',pair:'Trading pair',type:'Side',time:'Time',adminOnly:'Demo analytics — not production data'},zh:{dashboard:'总览',trade:'交易',wallet:'资产',deposit:'充值',withdraw:'提现',admin:'后台管理',markets:'市场行情',balance:'资产总估值',volume:'24小时成交额',change:'24小时涨跌',order:'提交订单',buy:'买入',sell:'卖出',amount:'金额（USDT）',price:'参考价格',submit:'提交模拟订单',history:'近期模拟订单',noorders:'暂无订单',demo:'演示模式 — 不涉及真实资金或订单',notice:'真实资金充值、提现及订单执行尚未启用，需先完成合规集成和安全控制。',depositMsg:'充值功能尚未开放，不提供钱包地址。',withdrawMsg:'提现功能尚未开放，不会转移任何资金。',adminMsg:'后台管理仅为界面预览。正式上线需要服务端身份验证及权限控制。',asset:'资产',quantity:'数量',value:'估算价值',status:'系统状态',operational:'前端运行正常',welcome:'清晰掌握数字资产市场',market:'市场',last:'最新价格',action:'操作',view:'查看',pair:'交易对',type:'方向',time:'时间',adminOnly:'演示分析数据，非真实生产数据'}};
const base=[{symbol:'BTC',name:'Bitcoin',price:96342.15,change:2.84,volume:'$28.4B',color:'#f59e0b'},{symbol:'ETH',name:'Ethereum',price:3218.44,change:1.56,volume:'$14.2B',color:'#818cf8'},{symbol:'SOL',name:'Solana',price:142.82,change:-0.78,volume:'$3.1B',color:'#a78bfa'},{symbol:'BNB',name:'BNB',price:614.27,change:0.92,volume:'$1.9B',color:'#eab308'},{symbol:'XRP',name:'XRP',price:2.18,change:-1.12,volume:'$2.6B',color:'#94a3b8'}];
const fmt=(n,d=2)=>Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const COIN_LIMIT = 50;
const COINS_PER_PAGE = 50;


async function 
async function fetch1000Coins() {
  const response = await fetch(
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=50&page=1&sparkline=false"
  );

  if (!response.ok) {
    if (response.status === 429) {
      throw new Error("CoinGecko rate limit. Please try again later.");
    }
    throw new Error(`CoinGecko API error: ${response.status}`);
  }

  return await response.json();
}
() {
  const allCoins = [];

  for (let page = 1; page <= 4; page++) {
    const response = await fetch(
      `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=${page}&sparkline=false`
    );

    if (!response.ok) {
      throw new Error("Failed to fetch coins");
    }

    const coins = await response.json();
    allCoins.push(...coins);
  }

  return allCoins.slice(0, COIN_LIMIT);
}

function App(){const [allCoins, setAllCoins] = useState([]);
const [coinSearch, setCoinSearch] = useState("");
const [coinPage, setCoinPage] = useState(1);
const [coinsLoading, setCoinsLoading] = useState(false);
const [coinsError, setCoinsError] = useState("");const[lang,setLang]=useState('en'),[tab,setTab]=useState('dashboard'),[coin,setCoin]=useState('BTC'),[side,setSide]=useState('buy'),[amount,setAmount]=useState('100'),[orders,setOrders]=useState([]),[prices,setPrices]=useState({}),[period,setPeriod]=useState('1D'),[mobile,setMobile]=useState(false),[error,setError]=useState('');const t=dict[lang];

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

               useEffect(()=>{let cancelled=false;async function refresh(){try{let r=await fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin,ripple&vs_currencies=usd&include_24hr_change=true');if(!r.ok)throw Error();let x=await r.json();if(!cancelled)setPrices({BTC:x.bitcoin,ETH:x.ethereum,SOL:x.solana,BNB:x.binancecoin,XRP:x.ripple})}catch{}}refresh();let id=setInterval(refresh,90000);return()=>{cancelled=true;clearInterval(id)}},[]);

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
 const selected =
  allCoins.find(
    x => x.symbol.toUpperCase() === coin
  )
    ? {
        ...allCoins.find(
          x => x.symbol.toUpperCase() === coin
        ),
        price: allCoins.find(
          x => x.symbol.toUpperCase() === coin
        ).current_price ?? 0,
        color: "#16c79a"
      }
    : markets.find(x => x.symbol === coin) || markets[0];const data=Array.from({length:48},(_,i)=>{const trend=Math.sin(i/5)*.014+Math.cos(i/9)*.009+(i/48-.5)*.026;return {time:`${String(Math.floor(i/2)).padStart(2,'0')}:00`,price:Math.round(selected.price*(1+trend)*100)/100}});
const nav=[['dashboard',LayoutDashboard],['trade',ChartCandlestick],['wallet',Wallet],['deposit',ArrowDownToLine],['withdraw',ArrowUpFromLine],['admin',Shield]];
function placeOrder(e){e.preventDefault();let n=Number(amount);if(!Number.isFinite(n)||n<=0||n>1000000){setError(lang==='zh'?'请输入 0 至 1,000,000 之间的有效金额':'Enter a valid amount between 0 and 1,000,000');return}setError('');setOrders(o=>[{id:crypto.randomUUID(),time:new Date().toLocaleString(),coin,side,amount:n,price:selected.price},...o]);setTab('trade')}
return <div className="app"><aside className={mobile?'sidebar open':'sidebar'}><div className="brand"><div className="brandmark">Z</div><span>Zen<span className="accent">Crypto</span></span></div><div className="navlabel">PLATFORM</div><nav>{nav.map(([key,Icon])=><button key={key} className={tab===key?'nav active':'nav'} onClick={()=>{setTab(key);setMobile(false)}}><Icon size={19}/>{t[key]}</button>)}</nav><div className="sidebarfoot"><span className="dot"/> {t.operational}<p>ZenCrypto v1.0 · Prototype</p></div></aside><main><header><button className="hamburger" onClick={()=>setMobile(!mobile)}><Menu size={23}/></button><div className="headerleft"><span className="breadcrumb">ZenCrypto / {t[tab]}</span><h1>{t[tab]}</h1></div><div className="headeractions"><span className="live"><span className="dot"/> MARKET DATA</span><button className="lang" onClick={()=>setLang(lang==='en'?'zh':'en')}><Globe size={16}/> {lang==='en'?'中文':'English'}</button><div className="avatar">Z</div></div></header><div className="content"><div className="warning"><Shield size={18}/><div><strong>{t.demo}</strong><p>{t.notice}</p></div></div>
{tab==='dashboard'&&<><div className="hero"><div><span className="eyebrow">ZENCRYPTO EXCHANGE</span><h2>{t.welcome}</h2><p>Bitcoin · Ethereum · Solana · Global markets</p><button onClick={()=>setTab('trade')}>{t.trade} →</button></div><div className="heroicon">₿</div></div><div className="stats"><div className="panel stat"><span>{t.balance}</span><strong>$0.00</strong><small>Demo account · No deposited assets</small></div><div className="panel stat"><span>{t.volume}</span><strong>$28.4B</strong><small>Illustrative BTC volume</small></div><div className="panel stat"><span>{t.status}</span><strong className="green">● Online</strong><small>{t.operational}</small></div></div><MarketTable 
markets={visibleCoins.map(c => ({
 id: c.id,
symbol: c.symbol.toUpperCase(),
  name: c.name,
  price: c.current_price ?? 0,
  change: c.price_change_percentage_24h ?? 0,
  volume: c.total_volume ?? 0,
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

{tab==='trade'&&<><div className="tradehead"><div><div className="coinbadge" style={{background:selected.color}}>{coin[0]}</div><div><h2>{coin}/USDT</h2><span>{selected.name}</span></div></div><div className="pricebig">${fmt(selected.price)} <span className={selected.change>=0?'green':'red'}>{selected.change>=0?'+':''}{fmt(selected.change)}%</span></div></div><div className="tradegrid"><div className="panel chartpanel"><div className="panelhead"><h3>{coin}/USDT <span className="muted">· {t.market}</span></h3><div className="periods">{['1H','1D','1W','1M'].map(x=><button className={period===x?'selected':''} key={x} onClick={()=>setPeriod(x)}>{x}</button>)}</div></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid stroke="#253147" strokeDasharray="3 4" vertical={false}/><XAxis dataKey="time" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} interval={9}/><YAxis domain={['auto','auto']} orientation="right" tick={{fill:'#8190a8',fontSize:11}} tickLine={false} axisLine={false} width={78} tickFormatter={v=>'$'+Math.round(v).toLocaleString()}/><Tooltip contentStyle={{background:'#1c273a',border:'1px solid #334155',borderRadius:10}} formatter={v=>['$'+fmt(v),'Price']}/><Line type="monotone" dataKey="price" stroke="#27d6a2" strokeWidth={2.5} dot={false}/></LineChart></ResponsiveContainer></div><p className="fine">Illustrative chart shape; latest reference price may be sourced from CoinGecko. Timeframe buttons are display-only.</p></div><div className="panel orderpanel"><h3>{t.order}</h3><div className="sidebuttons"><button className={side==='buy'?'buy selected':'buy'} onClick={()=>setSide('buy')}>{t.buy}</button><button className={side==='sell'?'sell selected':'sell'} onClick={()=>setSide('sell')}>{t.sell}</button></div><form onSubmit={placeOrder}><label>{t.pair}</label><select value={coin} onChange={e=>setCoin(e.target.value)}>allCoins.map(x=><option key={x.id} value={x.symbol.toUpperCase()}>{x.symbol}/USDT</option>)}</select><label>{t.price}</label><div className="readonly">${fmt(selected.price)} USD</div><label>{t.amount}</label><input type="number" min="0.01" max="1000000" step="any" value={amount} onChange={e=>setAmount(e.target.value)}/>{error&&<p className="red">{error}</p>}<button className={side==='buy'?'submit buy':'submit sell'}>{t.submit}</button></form><p className="fine">No execution, settlement, or custody occurs.</p></div></div><div className="panel orders"><h3>{t.history}</h3>{orders.length===0?<p className="empty">{t.noorders}</p>:<div className="tablewrap"><table><thead><tr><th>{t.time}</th><th>{t.pair}</th><th>{t.type}</th><th>{t.amount}</th><th>{t.price}</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}><td>{o.time}</td><td>{o.coin}/USDT</td><td className={o.side==='buy'?'green':'red'}>{o.side.toUpperCase()}</td><td>${fmt(o.amount)}</td><td>${fmt(o.price)}</td></tr>)}</tbody></table></div>}</div></>}
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
