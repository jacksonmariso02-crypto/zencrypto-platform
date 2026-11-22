import React,{useEffect,useMemo,useState} from "react";
import{createRoot}from"react-dom/client";

import{LayoutDashboard,ChartCandlestick,Wallet,ArrowDownToLine,ArrowUpFromLine,Shield,Globe,Menu}from"lucide-react";
import"./style.css";
const API=import.meta.env.VITE_API_BASE_URL||"https://zencrypto-api.onrender.com";
const EXCHANGE_API=(import.meta.env.VITE_EXCHANGE_API_BASE_URL||"").replace(/\/+$/, "");
const SEED=[{symbol:"BTC",name:"Bitcoin",price:0,change:0,quoteVolume:0,high:0,low:0},{symbol:"ETH",name:"Ethereum",price:0,change:0,quoteVolume:0,high:0,low:0},{symbol:"SOL",name:"Solana",price:0,change:0,quoteVolume:0,high:0,low:0},{symbol:"BNB",name:"BNB",price:0,change:0,quoteVolume:0,high:0,low:0},{symbol:"XRP",name:"XRP",price:0,change:0,quoteVolume:0,high:0,low:0}];
const fmt=(n,d=2)=>Number(n||0).toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtCompact=n=>Number(n||0).toLocaleString("en-US",{notation:"compact",maximumFractionDigits:2});
function ema(values,period){
 const k=2/(period+1);let prev=null;
 return values.map(v=>{prev=prev===null?v:v*k+prev*(1-k);return prev;});
}
function sma(values,period){
 return values.map((_,i)=>i+1<period?null:values.slice(i-period+1,i+1).reduce((a,b)=>a+b,0)/period);
}
function rsiSeries(values,period=14){
 const out=values.map(()=>null);if(values.length<=period)return out;
 let gain=0,loss=0;
 for(let i=1;i<=period;i++){const d=values[i]-values[i-1];gain+=Math.max(d,0);loss+=Math.max(-d,0);}
 let avgGain=gain/period,avgLoss=loss/period;
 out[period]=avgLoss===0?100:100-100/(1+avgGain/avgLoss);
 for(let i=period+1;i<values.length;i++){const d=values[i]-values[i-1];avgGain=(avgGain*(period-1)+Math.max(d,0))/period;avgLoss=(avgLoss*(period-1)+Math.max(-d,0))/period;out[i]=avgLoss===0?100:100-100/(1+avgGain/avgLoss);}
 return out;
}
function CandlestickChart({candles,timeframe}){
 const [showEMA,setShowEMA]=useState(true),[showBB,setShowBB]=useState(false),[showVolume,setShowVolume]=useState(true),[showRSI,setShowRSI]=useState(false),[showMACD,setShowMACD]=useState(false),[showSMA,setShowSMA]=useState(false),[showVWAP,setShowVWAP]=useState(false),[hover,setHover]=useState(null),[drawingMode,setDrawingMode]=useState(false),[priceLevels,setPriceLevels]=useState([]),[visibleCount,setVisibleCount]=useState(120),[panOffset,setPanOffset]=useState(0);
 const W=1000,priceH=300,volH=showVolume?82:0,rsiH=showRSI?100:0,macdH=showMACD?100:0,top=18,gap=18,priceBottom=top+priceH,volTop=priceBottom+gap, rsiTop=volTop+volH+(showRSI?gap:0),macdTop=rsiTop+rsiH+(showMACD?gap:0),H=macdTop+macdH+42;
 const calc=useMemo(()=>{
  const rows=candles.slice(-500);const closes=rows.map(x=>Number(x.close)),volumes=rows.map(x=>Number(x.volume)||0);
  if(!rows.length)return {rows:[],ema9:[],ema21:[],ema50:[],sma20:[],vwap:[],upper:[],lower:[],rsi:[],macd:[],signal:[],hist:[],maxVol:1,min:0,max:1,step:1};
  const e9=ema(closes,9),e21=ema(closes,21),e50=ema(closes,50),mid=sma(closes,20);
  const variance=closes.map((_,i)=>i<19?null:Math.sqrt(closes.slice(i-19,i+1).reduce((a,v)=>a+(v-mid[i])**2,0)/20));
  let cumulativePV=0,cumulativeVolume=0;const vwap=rows.map((r)=>{const typical=(Number(r.high)+Number(r.low)+Number(r.close))/3,vol=Number(r.volume)||0;cumulativePV+=typical*vol;cumulativeVolume+=vol;return cumulativeVolume?cumulativePV/cumulativeVolume:null;});
  const upper=mid.map((v,i)=>v===null?null:v+2*variance[i]),lower=mid.map((v,i)=>v===null?null:v-2*variance[i]);
  const fast=ema(closes,12),slow=ema(closes,26),m=fast.map((v,i)=>v-slow[i]),sig=ema(m,9),hist=m.map((v,i)=>v-sig[i]);
  const all=rows.flatMap(x=>[Number(x.high),Number(x.low)]).filter(Number.isFinite);
  const min=Math.min(...all),max=Math.max(...all),pad=(max-min)*.08||Math.max(max*.002,.01);
  return {rows,ema9:e9,ema21:e21,ema50:e50,sma20:mid,vwap,upper,lower,rsi:rsiSeries(closes),macd:m,signal:sig,hist,maxVol:Math.max(...volumes,1),min:min-pad,max:max+pad,step:(max-min+2*pad)/4};
 },[candles]);
 const view=useMemo(()=>{
  const total=calc.rows.length,count=Math.min(visibleCount,total),end=Math.max(0,total-Math.min(panOffset,Math.max(0,total-count))),start=Math.max(0,end-count);
  const rows=calc.rows.slice(start,end),slice=key=>calc[key].slice(start,end),all=rows.flatMap(v=>[Number(v.high),Number(v.low)]).filter(Number.isFinite);
  const rawMin=all.length?Math.min(...all):0,rawMax=all.length?Math.max(...all):1,pad=(rawMax-rawMin)*.08||Math.max(rawMax*.002,.01);
  return {rows,ema9:slice("ema9"),ema21:slice("ema21"),ema50:slice("ema50"),sma20:slice("sma20"),vwap:slice("vwap"),upper:slice("upper"),lower:slice("lower"),rsi:slice("rsi"),macd:slice("macd"),signal:slice("signal"),hist:slice("hist"),maxVol:Math.max(1,...rows.map(v=>Number(v.volume)||0)),min:rawMin-pad,max:rawMax+pad,start,end,total};
 },[calc,visibleCount,panOffset]);
 useEffect(()=>{setHover(null);setPanOffset(0);},[timeframe]);
 const n=view.rows.length,plotW=W-86,left=8,right=plotW+8,x=i=>left+(i+.5)*plotW/Math.max(n,1),y=v=>top+(view.max-v)/(view.max-view.min)*priceH,vy=v=>volTop+volH-(v/view.maxVol)*Math.max(volH-8,1),ry=v=>rsiTop+(100-v)/100*Math.max(rsiH-20,1),my=v=>macdTop+macdH/2-v*(macdH*.38)/(Math.max(0.00000001,...view.macd.map(Math.abs),...view.signal.map(Math.abs)));
 const linePath=(arr,mapper)=>arr.map((v,i)=>v===null||!Number.isFinite(v)?"":(i===0||arr[i-1]===null?"M":"L")+x(i).toFixed(1)+","+mapper(v).toFixed(1)).filter(Boolean).join(" ");
 const toggle=(label,value,set)=> <button type="button" className={"zc-indicator "+(value?"active":"")} onClick={()=>set(!value)}>{label}</button>;
 const active=hover===null?null:view.rows[hover];
 const onMove=e=>{const rect=e.currentTarget.getBoundingClientRect(),pos=(e.clientX-rect.left)/rect.width*W;const index=Math.max(0,Math.min(n-1,Math.floor((pos-left)/plotW*n)));setHover(index);};
 const formatTime=v=>new Date(v).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
 if(!n)return <div className="zc-chart-empty">Waiting for historical OHLC market data…</div>;
 return <div className="zc-chart-shell">
  <div className="zc-chart-toolbar">
   <div className="zc-indicator-group">{toggle("EMA 9/21/50",showEMA,setShowEMA)}{toggle("SMA 20",showSMA,setShowSMA)}{toggle("VWAP",showVWAP,setShowVWAP)}{toggle("Bollinger",showBB,setShowBB)}{toggle("Volume",showVolume,setShowVolume)}{toggle("RSI 14",showRSI,setShowRSI)}{toggle("MACD 12/26/9",showMACD,setShowMACD)}</div>
   <div className="zc-chart-nav"><button type="button" title="Older candles" disabled={view.start===0} onClick={()=>{setPanOffset(v=>Math.min(Math.max(0,view.total-visibleCount),v+Math.ceil(visibleCount/2)));setHover(null);}}>← Older</button><button type="button" title="Newer candles" disabled={panOffset===0} onClick={()=>{setPanOffset(v=>Math.max(0,v-Math.ceil(visibleCount/2)));setHover(null);}}>Newer →</button><button type="button" title="Zoom out" disabled={visibleCount>=300} onClick={()=>setVisibleCount(v=>Math.min(300,v+30))}>−</button><button type="button" title="Zoom in" disabled={visibleCount<=30} onClick={()=>setVisibleCount(v=>Math.max(30,v-30))}>＋</button><button type="button" title="Reset chart view" onClick={()=>{setVisibleCount(120);setPanOffset(0);setHover(null);}}>Reset</button><span className="zc-chart-count">{n}/{view.total} candles · {timeframe}</span></div>
   <div className="zc-drawing-tools"><button type="button" className={drawingMode?"active":""} onClick={()=>setDrawingMode(v=>!v)}>＋ Price level</button><button type="button" disabled={!priceLevels.length} onClick={()=>setPriceLevels([])}>Clear levels ({priceLevels.length})</button></div>
  </div>
  <div className="zc-chart-legend">
   <span><i className="zc-dot zc-up"/> Bullish</span><span><i className="zc-dot zc-down"/> Bearish</span>
   {showEMA&&<><span className="ema9-key">EMA 9</span><span className="ema21-key">EMA 21</span><span className="ema50-key">EMA 50</span></>}
   {showSMA&&<span className="sma-key">SMA 20</span>}{showVWAP&&<span className="vwap-key">VWAP</span>}{showBB&&<span className="bb-key">Bollinger Bands</span>}{drawingMode&&<span className="drawing-hint">Click chart to place a horizontal level</span>}
  </div>
  <div className="zc-chart-readout">{active?<><b>{formatTime(active.timestamp)}</b><span>O {fmt(active.open,4)}</span><span>H {fmt(active.high,4)}</span><span>L {fmt(active.low,4)}</span><span>C {fmt(active.close,4)}</span><span>Vol {fmt(active.volume,2)}</span></>:<span>Move cursor over chart to inspect a candle</span>}</div>
  <svg className="zc-candle-svg" viewBox={"0 0 "+W+" "+H} role="img" aria-label="Interactive candlestick chart with volume and technical indicators" onMouseMove={onMove} onMouseLeave={()=>setHover(null)} onClick={e=>{if(!drawingMode)return;const rect=e.currentTarget.getBoundingClientRect(),svgY=(e.clientY-rect.top)/rect.height*H,price=view.max-((svgY-top)/priceH)*(view.max-view.min);if(price>view.min&&price<view.max){setPriceLevels(v=>[...v,{id:Date.now(),price}]);setDrawingMode(false);}}} preserveAspectRatio="none">
   {[0,1,2,3,4].map(i=>{const val=view.max-(view.max-view.min)*i/4,yy=top+priceH*i/4;return <g key={"grid"+i}><line x1={left} x2={plotW+12} y1={yy} y2={yy} stroke="#28364a" strokeDasharray="4 5"/><text x={W-6} y={yy+4} fill="#8b9bb2" fontSize="12" textAnchor="end">{fmt(val,2)}</text></g>;})}
   {showBB&&<><path d={linePath(view.upper,y)} fill="none" stroke="#a78bfa" strokeWidth="1.2" strokeDasharray="4 3"/><path d={linePath(view.lower,y)} fill="none" stroke="#a78bfa" strokeWidth="1.2" strokeDasharray="4 3"/></>}
   {showVWAP&&<path d={linePath(view.vwap,y)} fill="none" stroke="#f472b6" strokeWidth="1.8"/>}
   {showSMA&&<path d={linePath(view.sma20,y)} fill="none" stroke="#fb923c" strokeWidth="1.5" strokeDasharray="5 3"/>}
   {showEMA&&<><path d={linePath(view.ema9,y)} fill="none" stroke="#fbbf24" strokeWidth="1.5"/><path d={linePath(view.ema21,y)} fill="none" stroke="#38bdf8" strokeWidth="1.5"/><path d={linePath(view.ema50,y)} fill="none" stroke="#c084fc" strokeWidth="1.5"/></>}
   {priceLevels.map(level=><g key={level.id}><line x1={left} x2={plotW+12} y1={y(level.price)} y2={y(level.price)} stroke="#fbbf24" strokeWidth="1.3" strokeDasharray="7 4"/><text x={W-6} y={y(level.price)-5} fill="#fbbf24" fontSize="11" textAnchor="end">{fmt(level.price,4)}</text></g>)}
   {view.rows.map((c,i)=>{const up=c.close>=c.open,color=up?"#27d6a2":"#fa7185",cx=x(i),bw=Math.max(1.4,Math.min(7,plotW/Math.max(n,1)*.68)),bodyY=y(Math.max(c.open,c.close)),bodyH=Math.max(1.2,Math.abs(y(c.open)-y(c.close)));return <g key={c.timestamp}><line x1={cx} x2={cx} y1={y(c.high)} y2={y(c.low)} stroke={color} strokeWidth="1.2"/><rect x={cx-bw/2} y={bodyY} width={bw} height={bodyH} fill={color} stroke={color}/></g>;})}
   {showVolume&&<><line x1={left} x2={plotW+12} y1={volTop} y2={volTop} stroke="#334155"/>{view.rows.map((c,i)=>{const bw=Math.max(1.4,Math.min(7,plotW/Math.max(n,1)*.68)),color=c.close>=c.open?"#27d6a2":"#fa7185",h=volTop+volH-vy(c.volume);return <rect key={"vol"+c.timestamp} x={x(i)-bw/2} y={vy(c.volume)} width={bw} height={Math.max(1,h)} fill={color} opacity=".65"/>;})}<text x={left} y={volTop+13} fill="#8b9bb2" fontSize="11">VOLUME</text></>}
   {showRSI&&<><line x1={left} x2={plotW+12} y1={ry(70)} y2={ry(70)} stroke="#64748b" strokeDasharray="4 4"/><line x1={left} x2={plotW+12} y1={ry(30)} y2={ry(30)} stroke="#64748b" strokeDasharray="4 4"/><path d={linePath(view.rsi,ry)} fill="none" stroke="#c084fc" strokeWidth="1.7"/><text x={left} y={rsiTop+12} fill="#c084fc" fontSize="11">RSI 14</text><text x={W-6} y={ry(70)+4} fill="#8b9bb2" fontSize="10" textAnchor="end">70</text><text x={W-6} y={ry(30)+4} fill="#8b9bb2" fontSize="10" textAnchor="end">30</text></>}
   {showMACD&&<><line x1={left} x2={plotW+12} y1={my(0)} y2={my(0)} stroke="#64748b"/>{view.hist.map((v,i)=>{const bw=Math.max(1.2,Math.min(6,plotW/Math.max(n,1)*.55)),zero=my(0),yy=my(v);return <rect key={"macd"+i} x={x(i)-bw/2} y={Math.min(zero,yy)} width={bw} height={Math.max(1,Math.abs(zero-yy))} fill={v>=0?"#27d6a2":"#fa7185"} opacity=".65"/>;})}<path d={linePath(view.macd,my)} fill="none" stroke="#38bdf8" strokeWidth="1.4"/><path d={linePath(view.signal,my)} fill="none" stroke="#fbbf24" strokeWidth="1.4"/><text x={left} y={macdTop+12} fill="#8b9bb2" fontSize="11">MACD 12/26/9</text></>}
   {hover!==null&&<><line x1={x(hover)} x2={x(hover)} y1={top} y2={H-24} stroke="#e2e8f0" strokeDasharray="3 4" opacity=".65"/><line x1={left} x2={plotW+12} y1={y(view.rows[hover].close)} y2={y(view.rows[hover].close)} stroke="#e2e8f0" strokeDasharray="3 4" opacity=".5"/><circle cx={x(hover)} cy={y(view.rows[hover].close)} r="3.5" fill="#e2e8f0"/></>}
   {[0,1,2,3,4].map(i=>{const idx=Math.round(i*(n-1)/4),c=view.rows[idx];return <text key={"time"+i} x={x(idx)} y={H-7} fill="#8b9bb2" fontSize="11" textAnchor="middle">{new Date(c.timestamp).toLocaleDateString([],{month:"short",day:"numeric"})}</text>;})}
  </svg>
  <div className="zc-chart-foot">Source: Binance public OHLCV · Prices are exchange-reported candles. Indicators are calculated locally from displayed data; they are not trading recommendations.</div>
 </div>;
}

function StrategyBacktest({candles,symbol,timeframe}){
 const [strategy,setStrategy]=useState("ema"),[fast,setFast]=useState("9"),[slow,setSlow]=useState("21"),[capital,setCapital]=useState("10000"),[fee,setFee]=useState("0.1"),[slippage,setSlippage]=useState("5");
 const result=useMemo(()=>{
  const f=Number(fast),sl=Number(slow),initial=Number(capital),feeRate=Number(fee)/100,slippageRate=Number(slippage)/10000;
  if(!Number.isInteger(f)||!Number.isInteger(sl)||f<2||sl<=f||sl>200||!Number.isFinite(initial)||initial<=0||!Number.isFinite(feeRate)||feeRate<0||feeRate>0.02||!Number.isFinite(slippageRate)||slippageRate<0||slippageRate>0.02||candles.length<sl+3)return null;
  const close=candles.map(x=>Number(x.close));if(close.some(v=>!Number.isFinite(v)||v<=0))return null;const ef=ema(close,f),es=ema(close,sl),rsi=rsiSeries(close,14),macdFast=ema(close,12),macdSlow=ema(close,26),macdLine=macdFast.map((v,i)=>v-macdSlow[i]),macdSignal=ema(macdLine,9);const signalAt=i=>strategy==="rsi"?{up:rsi[i-2]!==null&&rsi[i-1]!==null&&rsi[i-2]<=30&&rsi[i-1]>30,down:rsi[i-2]!==null&&rsi[i-1]!==null&&rsi[i-2]>=70&&rsi[i-1]<70}:strategy==="macd"?{up:macdLine[i-2]<=macdSignal[i-2]&&macdLine[i-1]>macdSignal[i-1],down:macdLine[i-2]>=macdSignal[i-2]&&macdLine[i-1]<macdSignal[i-1]}:{up:ef[i-2]<=es[i-2]&&ef[i-1]>es[i-1],down:ef[i-2]>=es[i-2]&&ef[i-1]<es[i-1]};
  let cash=initial,qty=0,entry=0,entryFee=0,peak=initial,maxDD=0;const trades=[],equity=[];
  for(let i=2;i<close.length;i++){
   const {up,down}=signalAt(i);
   if(!qty&&up){const execution=close[i]*(1+slippageRate),spend=cash/(1+feeRate);qty=spend/execution;entry=execution;entryFee=spend*feeRate;cash=0;trades.push({type:"BUY",price:execution,time:candles[i].timestamp,qty});}
   else if(qty&&down){const execution=close[i]*(1-slippageRate),gross=qty*execution,exitFee=gross*feeRate,net=gross-exitFee,profit=net-(qty*entry+entryFee);cash=net;trades.push({type:"SELL",price:execution,time:candles[i].timestamp,qty,profit});qty=0;entry=0;entryFee=0;}
   const value=cash+qty*close[i];peak=Math.max(peak,value);maxDD=Math.max(maxDD,peak?((peak-value)/peak):0);
   equity.push({time:candles[i].timestamp,value,benchmark:initial*close[i]/close[0]});
  }
  const last=close[close.length-1],openPnL=qty?qty*last-(qty*entry+entryFee):0,ending=cash+qty*last,net=ending-initial,completed=trades.filter(t=>t.type==="SELL"),wins=completed.filter(t=>t.profit>0),losses=completed.filter(t=>t.profit<0),grossWin=wins.reduce((a,t)=>a+t.profit,0),grossLoss=Math.abs(losses.reduce((a,t)=>a+t.profit,0)),avgWin=wins.length?grossWin/wins.length:0,avgLoss=losses.length?grossLoss/losses.length:0,benchmark=initial*last/close[0];
  const splitIndex=Math.max(sl+2,Math.floor(close.length*.7)),testStart=Math.min(close.length-1,splitIndex);
  let testCash=initial,testQty=0,testEntry=0,testEntryFee=0,testPeak=initial,testMaxDD=0;const testTrades=[];
  for(let i=testStart;i<close.length;i++){
   const {up,down}=i>=2?signalAt(i):{up:false,down:false};
   if(!testQty&&up){const execution=close[i]*(1+slippageRate),spend=testCash/(1+feeRate);testQty=spend/execution;testEntry=execution;testEntryFee=spend*feeRate;testCash=0;testTrades.push({type:"BUY",price:execution,time:candles[i].timestamp});}
   else if(testQty&&down){const execution=close[i]*(1-slippageRate),gross=testQty*execution,net=gross*(1-feeRate),profit=net-(testQty*testEntry+testEntryFee);testCash=net;testTrades.push({type:"SELL",price:execution,time:candles[i].timestamp,profit});testQty=0;testEntry=0;testEntryFee=0;}
   const equityValue=testCash+testQty*close[i];testPeak=Math.max(testPeak,equityValue);testMaxDD=Math.max(testMaxDD,testPeak?(testPeak-equityValue)/testPeak:0);
  }
  const testEnd=testCash+testQty*close[close.length-1],testClosed=testTrades.filter(t=>t.type==="SELL"),testWins=testClosed.filter(t=>t.profit>0);
  const testBenchmark=initial*close[close.length-1]/close[testStart];
  return {initial,ending,net,splitPct:Math.round(testStart/close.length*100),oosEnding:testEnd,oosReturnPct:(testEnd/initial-1)*100,oosMaxDD:testMaxDD*100,oosTrades:testClosed.length,oosWinRate:testClosed.length?testWins.length/testClosed.length*100:0,oosBenchmarkReturn:(testBenchmark/initial-1)*100,returnPct:(ending/initial-1)*100,maxDD:maxDD*100,completed,wins,winRate:completed.length?wins.length/completed.length*100:0,profitFactor:grossLoss?grossWin/grossLoss:grossWin?Infinity:0,expectancy:completed.length?completed.reduce((a,t)=>a+t.profit,0)/completed.length:0,avgWin,avgLoss,benchmark,benchmarkReturn:(benchmark/initial-1)*100,equity,slippageBps:Number(slippage),trades:trades.slice().reverse(),open:qty>0,openPnL};
 },[candles,strategy,fast,slow,capital,fee,slippage]);
 const exportCsv=()=>{if(!result)return;const rows=[["side","timestamp","execution_price","quantity","realized_pnl"],...result.trades.map(t=>[t.type,new Date(t.time).toISOString(),t.price,t.qty??"",t.profit??""])];const csv=rows.map(row=>row.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(",")).join("\n");const blob=new Blob([csv],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=`${symbol}-${strategy}-backtest-trades.csv`;link.click();URL.revokeObjectURL(url);};
 return <div className="panel backtest-panel"><div className="panelhead"><div><h3>Historical strategy backtest</h3><p className="muted">{symbol}/USDT · {strategy==="ema"?"EMA crossover":strategy==="rsi"?"RSI 14 reversal (30/70)":"MACD (12,26,9) crossover"} · {timeframe} candles · long-only</p></div><span className="backtest-tag">RESEARCH ONLY</span></div><div className="backtest-controls"><label>Strategy<select value={strategy} onChange={e=>setStrategy(e.target.value)}><option value="ema">EMA crossover</option><option value="rsi">RSI 14 reversal (30/70)</option><option value="macd">MACD (12,26,9) crossover</option></select></label><label>Fast EMA<input type="number" min="2" max="199" value={fast} onChange={e=>setFast(e.target.value)}/></label><label>Slow EMA<input type="number" min="3" max="200" value={slow} onChange={e=>setSlow(e.target.value)}/></label><label>Starting capital (USD)<input type="number" min="1" step="100" value={capital} onChange={e=>setCapital(e.target.value)}/></label><label>Fee per side (%)<input type="number" min="0" max="2" step="0.01" value={fee} onChange={e=>setFee(e.target.value)}/></label><label>Slippage (basis points)<input type="number" min="0" max="200" step="1" value={slippage} onChange={e=>setSlippage(e.target.value)}/></label></div>{!result?<p className="empty">Need at least slow EMA + 3 valid candles. Fast EMA must be smaller than slow EMA (maximum 200).</p>:<><div className="backtest-metrics"><div><span>Ending equity</span><b>{"$"+fmt(result.ending,2)}</b></div><div><span>Net return</span><b className={result.net>=0?"green":"red"}>{result.returnPct>=0?"+":""}{fmt(result.returnPct,2)}%</b></div><div><span>Max drawdown</span><b className="red">{fmt(result.maxDD,2)}%</b></div><div><span>Win rate</span><b>{fmt(result.winRate,1)}%</b></div><div><span>Closed trades</span><b>{result.completed.length}</b></div><div><span>Profit factor</span><b>{Number.isFinite(result.profitFactor)?fmt(result.profitFactor,2):"∞"}</b></div><div><span>Buy & hold return</span><b className={result.benchmarkReturn>=0?"green":"red"}>{fmt(result.benchmarkReturn,2)}%</b></div><div><span>Trade expectancy</span><b>{"$"+fmt(result.expectancy,2)}</b></div><div><span>Avg win / loss</span><b>{"$"+fmt(result.avgWin,2)+" / $"+fmt(result.avgLoss,2)}</b></div><div><span>Out-of-sample return</span><b className={result.oosReturnPct>=0?"green":"red"}>{fmt(result.oosReturnPct,2)}%</b></div><div><span>Out-of-sample max DD</span><b className="red">{fmt(result.oosMaxDD,2)}%</b></div><div><span>Out-of-sample trades</span><b>{result.oosTrades}</b></div><div><span>OOS win rate</span><b>{fmt(result.oosWinRate,1)}%</b></div><div><span>OOS buy & hold</span><b className={result.oosBenchmarkReturn>=0?"green":"red"}>{fmt(result.oosBenchmarkReturn,2)}%</b></div></div><div className="equity-curve"><div className="equity-title"><strong>Equity curve vs buy & hold</strong><span>Marked to market · USD</span></div><svg viewBox="0 0 760 190" role="img" aria-label="Backtest strategy equity compared with buy and hold" className="equity-svg">{(()=>{const vals=result.equity.flatMap(x=>[x.value,x.benchmark]),lo=Math.min(result.initial,...vals),hi=Math.max(result.initial,...vals),pad=(hi-lo)*.08||1,min=lo-pad,max=hi+pad,x=i=>18+i/Math.max(1,result.equity.length-1)*724,y=v=>12+(max-v)/(max-min)*142,path=k=>result.equity.map((v,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(v[k]).toFixed(1)).join(" ");return <><line x1="18" x2="742" y1="154" y2="154" stroke="#334155"/><path d={path("benchmark")} fill="none" stroke="#94a3b8" strokeWidth="2" strokeDasharray="5 4"/><path d={path("value")} fill="none" stroke="#27d6a2" strokeWidth="2.2"/><text x="18" y="178" fill="#8190a8" fontSize="10">Start</text><text x="742" y="178" fill="#8190a8" fontSize="10" textAnchor="end">Latest</text></>;})()}</svg><div className="equity-legend"><span className="green">● Strategy</span><span>● Buy & hold</span></div></div><div className="backtest-note">{result.open?"Open position marked to market. Unrealized P/L: $"+fmt(result.openPnL,2):"No open position at the end of the sample."} · Chronological split: first 70% in-sample, final 30% out-of-sample (OOS). OOS starts with fresh cash; indicators use prior history for warm-up. · Includes configured taker fees and adverse execution slippage of {result.slippageBps} bps per side. Assumes next-bar close fills; excludes spread, market impact, funding and taxes. Benchmark assumes buy-and-hold from first candle close and excludes fees and slippage.</div><div className="backtest-trades"><div className="backtest-trades-head"><strong>Latest strategy signals</strong><button type="button" className="export-csv" onClick={exportCsv}>Export CSV</button></div>{result.trades.length?result.trades.slice(0,8).map((t,i)=><div className="backtest-trade-row" key={i}><span className={t.type==="BUY"?"green":"red"}>{t.type}</span><span>{fmt(t.price,4)}</span><span>{new Date(t.time).toLocaleString()}</span><span>{t.type==="SELL"?(t.profit>=0?"+":"")+"$"+fmt(t.profit,2):"—"}</span></div>):<p className="empty">No strategy signals in this sample.</p>}</div></>}</div>;
}

function App(){
 const[lang,setLang]=useState("en"),[tab,setTab]=useState("dashboard"),[mobile,setMobile]=useState(false),[token,setToken]=useState(localStorage.getItem("zencrypto_token")||""),[user,setUser]=useState(null),[exchangeConnection,setExchangeConnection]=useState(null),[exchangeConnectionLoading,setExchangeConnectionLoading]=useState(false),[exchangeApiKey,setExchangeApiKey]=useState(""),[exchangeApiSecret,setExchangeApiSecret]=useState(""),[exchangeConnectBusy,setExchangeConnectBusy]=useState(false),[exchangeConnectMessage,setExchangeConnectMessage]=useState(""),[exchangeBalances,setExchangeBalances]=useState([]),[exchangeBalancesLoading,setExchangeBalancesLoading]=useState(false),[walletSummary,setWalletSummary]=useState(null),[walletLoading,setWalletLoading]=useState(false),[walletMessage,setWalletMessage]=useState(""),[authOpen,setAuthOpen]=useState(false),[authMode,setAuthMode]=useState("login"),[email,setEmail]=useState(""),[password,setPassword]=useState(""),[authMessage,setAuthMessage]=useState(""),[authBusy,setAuthBusy]=useState(false),[authChecking,setAuthChecking]=useState(Boolean(localStorage.getItem("zencrypto_token"))),[markets,setMarkets]=useState(SEED),[tickerStatus,setTickerStatus]=useState("connecting"),[tickerUpdatedAt,setTickerUpdatedAt]=useState(null),[exchangeStatus,setExchangeStatus]=useState(null),[exchangeStatusLoading,setExchangeStatusLoading]=useState(true),[exchangeStatusError,setExchangeStatusError]=useState(""),[symbol,setSymbol]=useState("BTC"),[marketInfo,setMarketInfo]=useState(null),[marketInfoStatus,setMarketInfoStatus]=useState("unconfigured"),[timeframe,setTimeframe]=useState("1h"),[candles,setCandles]=useState([]),[chartRefresh,setChartRefresh]=useState(0),[chartLoading,setChartLoading]=useState(false),[chartError,setChartError]=useState(""),[side,setSide]=useState("buy"),[amount,setAmount]=useState("100"),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[balances,setBalances]=useState([]),[orders,setOrders]=useState([]),[limitOrders,setLimitOrders]=useState([]),[limitTrades,setLimitTrades]=useState([]),[paperBook,setPaperBook]=useState({bids:[],asks:[]}),[paperBookUpdatedAt,setPaperBookUpdatedAt]=useState(null),[limitQty,setLimitQty]=useState("0.001"),[limitPrice,setLimitPrice]=useState(""),[limitBusy,setLimitBusy]=useState(false),[limitError,setLimitError]=useState(""),[tradeSummary,setTradeSummary]=useState(null),[orderOffset,setOrderOffset]=useState(0),[orderTotal,setOrderTotal]=useState(0),[orderSideFilter,setOrderSideFilter]=useState(""),[orderSymbolFilter,setOrderSymbolFilter]=useState(""),[orderStatusFilter,setOrderStatusFilter]=useState(""),[orderLoading,setOrderLoading]=useState(false),[orderExporting,setOrderExporting]=useState(false),[ledger,setLedger]=useState([]),[ledgerTotal,setLedgerTotal]=useState(0),[ledgerOffset,setLedgerOffset]=useState(0),[ledgerCurrency,setLedgerCurrency]=useState(""),[ledgerLoading,setLedgerLoading]=useState(false),[ledgerError,setLedgerError]=useState(""),[search,setSearch]=useState(""),[orderBook,setOrderBook]=useState(null),[recentTrades,setRecentTrades]=useState([]),[depthError,setDepthError]=useState(""),[depthUpdated,setDepthUpdated]=useState(null),[riskPercent,setRiskPercent]=useState("1"),[stopPrice,setStopPrice]=useState(""),[targetPrice,setTargetPrice]=useState(""),[customerOrderSide,setPreviewSide]=useState("buy"),[customerOrderType,setPreviewType]=useState("market"),[orderPreviewQty,setOrderPreviewQty]=useState("0.001"),[customerOrderPrice,setPreviewPrice]=useState(""),[customerOrderBusy,setPreviewBusy]=useState(false),[customerOrderResult,setPreviewResult]=useState(null),[customerOrderError,setPreviewError]=useState(""),[customerOrders,setCustomerOrders]=useState([]),[customerOrdersLoading,setCustomerOrdersLoading]=useState(false),[customerOrderActionId,setCustomerOrderActionId]=useState(""),[customerOrderSubmitBusy,setCustomerOrderSubmitBusy]=useState(false),[customerOrderSubmitResult,setCustomerOrderSubmitResult]=useState(null),[customerOrderIdempotencyKey,setCustomerOrderIdempotencyKey]=useState("");
 const t=lang==="en"?{overview:"Overview",trade:"Trade",assets:"Assets",deposit:"Deposit",withdraw:"Withdraw",admin:"Admin",buy:"Buy",sell:"Sell",amount:"Order value (USD)",history:"Trade history",empty:"No trades yet",paper:"PAPER TRADING — Simulated funds only",disclaimer:"All balances and trades are simulated. Real deposits, withdrawals, custody and live execution are disabled."}:{overview:"总览",trade:"交易",assets:"资产",deposit:"充值",withdraw:"提现",admin:"管理",buy:"买入",sell:"卖出",amount:"订单金额（USD）",history:"成交记录",empty:"暂无成交记录",paper:"模拟交易模式 — 仅使用模拟资金",disclaimer:"所有余额和交易均为模拟数据。真实充值、提现、托管及实盘交易均已关闭。"};
 async function requestJson(url,opts,timeoutMs,errorLabel){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
   return await fetch(url,{...(opts||{}),signal:controller.signal,cache:"no-store",credentials:"omit"});
  }catch(error){
   if(error?.name==="AbortError")throw Error(errorLabel+" timed out. Check the account or order history before repeating any write request.");
   throw error;
  }finally{clearTimeout(timeout);}
 }
 async function api(path,opts){
  const r=await requestJson(API+path,{...(opts||{}),headers:{...((opts&&opts.headers)||{}),...(token?{Authorization:"Bearer "+token}:{})}},15000,"Platform API request");
  const d=await r.json().catch(()=>({detail:"Invalid API response"}));
  if(!r.ok)throw Error(d.detail||"Request failed");
  return d;
 }

 async function exchangeApi(path,opts){
  if(!EXCHANGE_API)throw Error("The exchange connector is not configured.");
  const response=await requestJson(EXCHANGE_API+path,{...(opts||{}),headers:{...((opts&&opts.headers)||{}),...(token?{Authorization:"Bearer "+token}:{})}},20000,"Exchange connector request");
  const data=await response.json().catch(()=>({detail:"Invalid exchange connector response"}));
  if(!response.ok)throw Error(data.detail||"Exchange connector request failed.");
  return data;
 }
 async function loadExchangeConnection(){
  if(!token||!EXCHANGE_API){setExchangeConnection(null);return;}
  setExchangeConnectionLoading(true);setExchangeConnectMessage("");setExchangeConnection(null);setExchangeBalances([]);
  try{const data=await exchangeApi("/api/v1/user/exchange-connections");setExchangeConnection(data.connections?.find(x=>x.exchange==="binance")||null);}
  catch(e){setExchangeConnection(null);setExchangeBalances([]);setExchangeConnectMessage(e.message);}
  finally{setExchangeConnectionLoading(false);}
 }
 async function connectBinance(e){
  e.preventDefault();setExchangeConnectMessage("");setError("");
  if(!token){setAuthOpen(true);return;}
  if(!exchangeApiKey.trim()||!exchangeApiSecret.trim()){setExchangeConnectMessage("Enter both your Binance API key and secret.");return;}
  setExchangeConnectBusy(true);
  try{
   const data=await exchangeApi("/api/v1/user/exchange-connections/binance",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({api_key:exchangeApiKey.trim(),api_secret:exchangeApiSecret.trim()})});
   setExchangeConnection(data);setExchangeBalances([]);setNotice("Binance account verified. API credentials were encrypted on the server.");setExchangeConnectMessage(data.message||"Account connected.");
  }catch(e){setExchangeConnectMessage(e.message||"Unable to connect Binance account.");}
  finally{setExchangeApiKey("");setExchangeApiSecret("");setExchangeConnectBusy(false);}
 }
 async function disconnectBinance(){
  if(!window.confirm("Disconnect Binance and delete the encrypted API credentials stored by ZenCrypto?"))return;
  setExchangeConnectBusy(true);
  try{const data=await exchangeApi("/api/v1/user/exchange-connections/binance",{method:"DELETE"});setExchangeConnection({connected:false,exchange:"binance"});setExchangeBalances([]);setNotice(data.message||"Binance disconnected.");}
  catch(e){setExchangeConnectMessage(e.message);}
  finally{setExchangeConnectBusy(false);}
 }

 async function requestOrderPreview(e){
  e.preventDefault();setPreviewError("");setPreviewResult(null);setCustomerOrderIdempotencyKey("");setCustomerOrderSubmitResult(null);
  if(!token){setAuthMode("login");setAuthOpen(true);return;}
  if(!exchangeConnection?.connected){setPreviewError("Connect your Binance account before requesting a preview.");return;}
  setPreviewBusy(true);
  try{
   const payload={symbol:symbol+"/USDT",side:customerOrderSide,order_type:customerOrderType,amount:orderPreviewQty};
   if(customerOrderType==="limit")payload.price=customerOrderPrice;
   const data=await exchangeApi("/api/v1/user/orders/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
   setPreviewResult(data);setCustomerOrderIdempotencyKey("zc-"+crypto.randomUUID());
  }catch(e){setPreviewError(e.message||"Unable to calculate order preview.");}
  finally{setPreviewBusy(false);}
 }
 async function submitCustomerOrder(){
  if(!customerOrderResult||!exchangeConnection?.connected)return;
  if(!exchangeConnection?.live_order_routing_enabled){setPreviewError("Live customer order routing is disabled by the server. No order was submitted.");return;}
  const details=customerOrderResult.side.toUpperCase()+" "+customerOrderResult.symbol+"\nAmount: "+customerOrderResult.amount+"\nEstimated notional: "+customerOrderResult.estimated_notional_usdt+" USDT\nReference price: "+customerOrderResult.reference_price+" USDT";
  if(!window.confirm("You are about to submit a REAL order using your connected Binance account.\n\n"+details+"\n\nMarket prices can change. Continue?"))return;
  setCustomerOrderSubmitBusy(true);setPreviewError("");setCustomerOrderSubmitResult(null);
  try{
   const key=customerOrderIdempotencyKey||("zc-"+crypto.randomUUID());if(!customerOrderIdempotencyKey)setCustomerOrderIdempotencyKey(key);
   const payload={symbol:customerOrderResult.symbol,side:customerOrderResult.side,order_type:customerOrderResult.order_type,amount:customerOrderResult.amount};
   if(customerOrderResult.order_type==="limit")payload.price=customerOrderPrice;
   const result=await exchangeApi("/api/v1/user/orders",{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key},body:JSON.stringify(payload)});
   setCustomerOrderSubmitResult(result);setCustomerOrderIdempotencyKey("");setPreviewResult(null);await loadCustomerOrders();
  }catch(e){setPreviewError(e.message||"Order submission was not confirmed. Check order history before retrying.");}
  finally{setCustomerOrderSubmitBusy(false);}
 }
 async function loadCustomerOrders(){
  if(!token||!EXCHANGE_API){setCustomerOrders([]);return;}
  setCustomerOrdersLoading(true);
  try{const data=await exchangeApi("/api/v1/user/orders");setCustomerOrders(data.orders||[]);}
  catch(e){setPreviewError(e.message||"Unable to load customer order history.");}
  finally{setCustomerOrdersLoading(false);}
 }
 async function refreshCustomerOrder(orderId){
  setCustomerOrderActionId(orderId);setPreviewError("");
  try{await exchangeApi("/api/v1/user/orders/"+encodeURIComponent(orderId)+"/refresh",{method:"POST"});await loadCustomerOrders();}
  catch(e){setPreviewError(e.message||"Unable to synchronize this order.");}
  finally{setCustomerOrderActionId("");}
 }
 async function cancelCustomerOrder(orderId){
  if(!window.confirm("Send a cancellation request to Binance for this order?"))return;
  setCustomerOrderActionId(orderId);setPreviewError("");
  try{await exchangeApi("/api/v1/user/orders/"+encodeURIComponent(orderId),{method:"DELETE"});await loadCustomerOrders();}
  catch(e){setPreviewError(e.message||"Unable to cancel this order. Synchronize its status before retrying.");}
  finally{setCustomerOrderActionId("");}
 }
 async function loadWalletSummary(){
  if(!token||!EXCHANGE_API){setWalletSummary(null);return;}
  setWalletLoading(true);setWalletMessage("");
  try{const data=await exchangeApi("/api/v1/user/wallet");setWalletSummary(data);}
  catch(e){setWalletMessage(e.message||"Unable to load wallet account.");}
  finally{setWalletLoading(false);}
 }
 async function loadExchangeBalances(){
  setExchangeBalancesLoading(true);setExchangeConnectMessage("");setExchangeBalances([]);
  try{const data=await exchangeApi("/api/v1/user/exchange-connections/binance/balances");setExchangeBalances(data.balances||[]);setNotice("Retrieved "+(data.balances||[]).length+" non-zero asset balances from Binance.");}
  catch(e){setExchangeBalances([]);setExchangeConnectMessage(e.message);}
  finally{setExchangeBalancesLoading(false);}
 }
 useEffect(()=>{if(token&&!authChecking){loadExchangeConnection();}else{setExchangeConnection(null);setExchangeBalances([]);}},[token,authChecking]);
 useEffect(()=>{if(token&&exchangeConnection?.connected){loadExchangeBalances();}else{setExchangeBalances([]);}},[token,exchangeConnection?.connected]);
 useEffect(()=>{if(token){loadWalletSummary();}else{setWalletSummary(null);}},[token]);
 useEffect(()=>{if(token){loadCustomerOrders();}else{setCustomerOrders([]);}},[token]);
 useEffect(()=>{
  let active=true,busy=false,timer=null;
  if(!EXCHANGE_API){setExchangeStatusError("Exchange connector URL is not configured.");setExchangeStatusLoading(false);return()=>{active=false;};}
  const checkConnector=async()=>{
   if(busy||!active)return;
   busy=true;
   try{
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),8000);
    try{
     const response=await fetch(EXCHANGE_API+"/health",{signal:controller.signal,cache:"no-store"});
     const health=await response.json().catch(()=>({}));
     if(!response.ok)throw Error("Health endpoint HTTP "+response.status+(health.detail?": "+String(health.detail):""));
     if(health.status!=="ok"||health.exchange!=="binance")throw Error("Unexpected exchange connector health response.");
     const statusResponse=await fetch(EXCHANGE_API+"/api/v1/market-data/status",{signal:controller.signal,cache:"no-store"});
     const status=await statusResponse.json().catch(()=>({}));
     if(!statusResponse.ok)throw Error("Market status HTTP "+statusResponse.status+(status.detail?": "+String(status.detail):""));
     if(!active)return;
     setExchangeStatus({...status,build_revision:health.build_revision,health_verified:true});
     setExchangeStatusError("");
    }finally{clearTimeout(timeout);}
   }catch(e){
    if(active){setExchangeStatusError(e.name==="AbortError"?"Exchange connector health check timed out.":e.message||"Connector unavailable.");setExchangeStatus(null);}
   }finally{
    busy=false;
    if(active)setExchangeStatusLoading(false);
   }
  };
  checkConnector();
  timer=setInterval(checkConnector,30000);
  return()=>{active=false;if(timer)clearInterval(timer);};
 },[]);
 useEffect(()=>{let dead=false;if(!token){setUser(null);setBalances([]);setOrders([]);setAuthChecking(false);return;}setAuthChecking(true);fetch(API+"/account/me",{headers:{Authorization:"Bearer "+token}}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||"Session expired");if(!dead)setUser(d);}).catch(()=>{if(!dead){localStorage.removeItem("zencrypto_token");setToken("");setUser(null);}}).finally(()=>{if(!dead)setAuthChecking(false);});return()=>{dead=true;};},[token]);
 useEffect(()=>{
  let ws=null,retryTimer=null,connectionTimer=null,stopped=false;
  function connect(){
   if(stopped)return;
   setTickerStatus("connecting");
   ws=new WebSocket("wss://stream.binance.com:443/ws/!ticker_1d@arr");
   connectionTimer=setTimeout(()=>{
    if(!stopped&&ws&&ws.readyState===WebSocket.CONNECTING){
     setTickerStatus("reconnecting");
     try{ws.close();}catch{}
    }
   },10000);
   ws.onopen=()=>{
    if(connectionTimer){clearTimeout(connectionTimer);connectionTimer=null;}
    if(!stopped)setTickerStatus("connecting");
   };
   ws.onmessage=e=>{
    try{
     const rows=JSON.parse(e.data);
     if(Array.isArray(rows)){
      const bySymbol=new Map(rows.filter(x=>x&&typeof x.s==="string").map(x=>[x.s,x]));
      const required=["BTC","ETH","SOL","BNB","XRP"];
      const valid=required.every(base=>{const q=bySymbol.get(base+"USDT");return q&&Number.isFinite(Number(q.c))&&Number(q.c)>0&&Number.isFinite(Number(q.P));});
      setMarkets(prev=>prev.map(m=>{const q=bySymbol.get(m.symbol+"USDT");return q&&Number.isFinite(Number(q.c))&&Number(q.c)>0&&Number.isFinite(Number(q.P))?{...m,price:Number(q.c),change:Number(q.P),quoteVolume:Number(q.q)||0,high:Number(q.h)||0,low:Number(q.l)||0}:m;}));
      if(valid){setTickerUpdatedAt(Date.now());setTickerStatus("live");}
      else{setTickerStatus(current=>current==="reconnecting"?"reconnecting":"stale");}
     }
    }catch{setTickerStatus("stale");}
   };
   ws.onclose=()=>{
    if(connectionTimer){clearTimeout(connectionTimer);connectionTimer=null;}
    if(!stopped){setTickerStatus("reconnecting");retryTimer=setTimeout(connect,5000);}
   };
   ws.onerror=()=>{if(ws)ws.close();};
  }
  connect();
  return()=>{
   stopped=true;
   if(retryTimer)clearTimeout(retryTimer);
   if(connectionTimer)clearTimeout(connectionTimer);
   if(ws){ws.onopen=null;ws.onmessage=null;ws.onerror=null;ws.onclose=null;try{ws.close();}catch{}}
  };
 },[]);
 useEffect(()=>{const watchdog=setInterval(()=>{if(!tickerUpdatedAt||Date.now()-tickerUpdatedAt>=15000){setTickerStatus(current=>current==="reconnecting"?"reconnecting":"stale");}},1000);return()=>clearInterval(watchdog);},[tickerUpdatedAt]);
 useEffect(()=>{
  let active=true,ws=null,retry=null,buffer=[],lastUpdateId=0,lastDepthMessageAt=0,synced=false,awaitingBridge=true,snapshotBusy=false;
  const bids=new Map(),asks=new Map(),symbolName=symbol.toLowerCase()+"usdt";
  function applySide(target,updates){for(const [priceText,qtyText] of updates){const price=Number(priceText),qty=Number(qtyText);if(!Number.isFinite(price)||!Number.isFinite(qty))continue;if(qty===0)target.delete(price);else target.set(price,qty);}}
  function applyEvent(event){applySide(bids,event.b||[]);applySide(asks,event.a||[]);lastUpdateId=event.u;lastDepthMessageAt=Date.now();publish();}
  function publish(){const sort=(map,desc)=>Array.from(map,([p,q])=>[String(p),String(q)]).sort((a,b)=>desc?Number(b[0])-Number(a[0]):Number(a[0])-Number(b[0])).slice(0,100);setOrderBook({lastUpdateId,bids:sort(bids,true),asks:sort(asks,false),synchronized:synced});setDepthUpdated(Date.now());if(synced)setDepthError("");}
  function processEvent(event){
   if(event.u<=lastUpdateId)return true;
   if(awaitingBridge){if(event.U>lastUpdateId+1||event.u<lastUpdateId+1)return false;awaitingBridge=false;synced=true;applyEvent(event);return true;}
   if(!synced)return false;
   if(event.pu!==undefined&&event.pu!==lastUpdateId)return false;
   if(event.U>lastUpdateId+1)return false;
   applyEvent(event);return true;
  }
  async function loadSnapshot(){
   if(snapshotBusy||!active)return;snapshotBusy=true;synced=false;awaitingBridge=true;setOrderBook(previous=>previous?{...previous,synchronized:false}:previous);buffer=[];
   try{
    const response=await fetch("https://api.binance.com/api/v3/depth?symbol="+symbol.toUpperCase()+"USDT&limit=100");
    if(!response.ok)throw Error("Binance order-book snapshot unavailable (HTTP "+response.status+").");
    const snapshot=await response.json();if(!active)return;
    bids.clear();asks.clear();applySide(bids,snapshot.bids||[]);applySide(asks,snapshot.asks||[]);
    lastUpdateId=Number(snapshot.lastUpdateId);if(!Number.isFinite(lastUpdateId))throw Error("Invalid order-book snapshot sequence.");
    const queued=buffer.slice();buffer=[];const relevant=queued.filter(e=>e.u>lastUpdateId);
    const bridgeIndex=relevant.findIndex(e=>e.U<=lastUpdateId+1&&e.u>=lastUpdateId+1);
    if(relevant.length&&bridgeIndex<0){snapshotBusy=false;retry=setTimeout(loadSnapshot,250);return;}
    if(bridgeIndex>=0){
     const first=relevant[bridgeIndex];awaitingBridge=false;synced=true;applyEvent(first);
     for(const event of relevant.slice(bridgeIndex+1)){if(event.u<=lastUpdateId)continue;if(event.pu!==undefined&&event.pu!==lastUpdateId){synced=false;awaitingBridge=true;snapshotBusy=false;retry=setTimeout(loadSnapshot,250);return;}if(event.U>lastUpdateId+1){synced=false;awaitingBridge=true;snapshotBusy=false;retry=setTimeout(loadSnapshot,250);return;}applyEvent(event);}
    }else{synced=false;awaitingBridge=true;setDepthError("Waiting for first sequence-valid depth update…");}
   }catch(e){if(active){setDepthError(e.message||"Unable to synchronize order book.");retry=setTimeout(loadSnapshot,3000);}}
   finally{snapshotBusy=false;}
  }
  function connect(){if(!active)return;ws=new WebSocket("wss://stream.binance.com:9443/ws/"+symbolName+"@depth@100ms");ws.onmessage=e=>{try{const event=JSON.parse(e.data);if(!active||!Number.isFinite(Number(event.U))||!Number.isFinite(Number(event.u)))return;if(!synced){buffer.push(event);if(buffer.length>2000)buffer.shift();return;}if(!processEvent(event)){synced=false;awaitingBridge=true;setDepthError("Order-book sequence gap detected. Resynchronizing…");loadSnapshot();}}catch{}};ws.onopen=()=>{setDepthError("Synchronizing order book…");loadSnapshot();};ws.onclose=()=>{synced=false;awaitingBridge=true;if(active)retry=setTimeout(connect,3000);};ws.onerror=()=>{if(ws)ws.close();};}
  connect();const watchdog=setInterval(()=>{if(active&&synced&&lastDepthMessageAt&&Date.now()-lastDepthMessageAt>5000){synced=false;awaitingBridge=true;setOrderBook(previous=>previous?{...previous,synchronized:false}:previous);setDepthError("Order-book feed is stale. Previous depth is hidden until a fresh snapshot and sequence bridge are verified.");loadSnapshot();}},1000);return()=>{active=false;clearInterval(watchdog);clearTimeout(retry);if(ws)ws.close();};
 },[symbol]);

 useEffect(()=>{
  let active=true;
  if(!EXCHANGE_API){setMarketInfo(null);setMarketInfoStatus("unconfigured");return()=>{active=false;};}
  setMarketInfoStatus("loading");setMarketInfo(null);
  fetch(EXCHANGE_API+"/api/v1/markets/"+symbol+"-USDT/info").then(async response=>{
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(data.detail||"Market rules unavailable.");
   if(active){setMarketInfo(data);setMarketInfoStatus("ready");}
  }).catch(()=>{if(active){setMarketInfo(null);setMarketInfoStatus("error");}});
  return()=>{active=false;};
 },[symbol]);
 useEffect(()=>{
  let active=true,controller=null,ws=null,retryTimer=null,retryDelay=1000,refreshTimer=null;
  const marketSymbol=symbol.toLowerCase()+"usdt";
  const parseKline=k=>{
   const timestamp=Number(k.t),open=Number(k.o),high=Number(k.h),low=Number(k.l),close=Number(k.c),volume=Number(k.v);
   if(!Number.isFinite(timestamp)||![open,high,low,close,volume].every(Number.isFinite)||close<=0||volume<0||high<low||low>Math.min(open,close)||high<Math.max(open,close))return null;
   return {timestamp,time:new Date(timestamp).toISOString(),open,high,low,close,price:close,volume};
  };
  async function loadCandles(){
   if(controller)controller.abort();
   controller=new AbortController();
   try{
    const response=await fetch("https://api.binance.com/api/v3/klines?symbol="+marketSymbol.toUpperCase()+"&interval="+timeframe+"&limit=500",{signal:controller.signal});
    if(!response.ok)throw Error("Binance market data returned HTTP "+response.status);
    const rows=await response.json();
    if(!Array.isArray(rows)||!rows.length)throw Error("No historical OHLC data is currently available.");
    const parsed=rows.map(k=>parseKline({t:k[0],o:k[1],h:k[2],l:k[3],c:k[4],v:k[5]})).filter(Boolean);
    if(!parsed.length)throw Error("Historical candle data could not be validated.");
    if(active){setCandles(parsed);setChartError("");}
   }catch(e){if(active&&e.name!=="AbortError")setChartError(e.message||"Unable to load historical market data.");}
   finally{if(active)setChartLoading(false);}
  }
  function scheduleReconnect(){
   if(!active||retryTimer)return;
   retryTimer=setTimeout(()=>{retryTimer=null;connectSocket();},retryDelay);
   retryDelay=Math.min(retryDelay*2,30000);
  }
  function connectSocket(){
   if(!active)return;
   try{
    ws=new WebSocket("wss://stream.binance.com:9443/ws/"+marketSymbol+"@kline_"+timeframe);
    ws.onopen=()=>{retryDelay=1000;};
    ws.onmessage=event=>{
     if(!active)return;
     try{
      const payload=JSON.parse(event.data),candle=payload?.k?parseKline(payload.k):null;
      if(!candle)return;
      setCandles(previous=>{
       const next=previous.slice(),last=next[next.length-1];
       if(last&&last.timestamp===candle.timestamp)next[next.length-1]=candle;
       else if(!last||candle.timestamp>last.timestamp)next.push(candle);
       else return previous;
       return next.slice(-500);
      });
      setChartError("");
     }catch{setChartError("A live candle update could not be read; waiting for the next update.");}
    };
    ws.onerror=()=>{try{ws?.close();}catch{}};
    ws.onclose=()=>{if(active)scheduleReconnect();};
   }catch{scheduleReconnect();}
  }
  setChartLoading(true);
  loadCandles();
  connectSocket();
  refreshTimer=setInterval(()=>{if(active)loadCandles();},60000);
  return()=>{
   active=false;
   if(controller)controller.abort();
   if(refreshTimer)clearInterval(refreshTimer);
   if(retryTimer)clearTimeout(retryTimer);
   if(ws){ws.onopen=null;ws.onmessage=null;ws.onclose=null;ws.onerror=null;ws.close();}
  };
 },[symbol,timeframe,chartRefresh]);

 const selected=markets.find(x=>x.symbol===symbol)||markets[0],tickerAgeMs=tickerUpdatedAt?Date.now()-tickerUpdatedAt:Infinity,quoteFresh=tickerStatus==="live"&&tickerAgeMs<15000,chart=candles,usd=Number(balances.find(x=>x.currency==="USD")?.amount||0),assetBalance=Number(balances.find(x=>x.currency===symbol)?.amount||0),portfolio=balances.reduce((s,b)=>s+(b.currency==="USD"?Number(b.amount):Number(b.amount)*(markets.find(m=>m.symbol===b.currency)?.price||0)),0),filtered=markets.filter(m=>(m.symbol+" "+m.name).toLowerCase().includes(search.toLowerCase()));
 const riskBudget=portfolio*(Number(riskPercent)||0)/100,entryPrice=Number(selected.price)||0,stopValue=Number(stopPrice),targetValue=Number(targetPrice),validStop=stopPrice!==""&&entryPrice>0&&(side==="buy"?stopValue>0&&stopValue<entryPrice:stopValue>entryPrice),riskDistance=validStop?Math.abs(entryPrice-stopValue):0,riskQuantity=riskDistance?riskBudget/riskDistance:0,riskNotional=riskQuantity*entryPrice,rewardDistance=targetPrice!==""&&targetValue>0?(side==="buy"?targetValue-entryPrice:entryPrice-targetValue):0,rewardRisk=validStop&&rewardDistance>0?rewardDistance/riskDistance:null;
 const customerOrderAmount=Number(amount),previewMinor=Math.round(customerOrderAmount*100),previewQty=entryPrice>0&&previewMinor>0?Math.floor((previewMinor/100/entryPrice)*1e8)/1e8:0,previewFee=previewMinor>0?Math.max(1,Math.floor(previewMinor*0.001))/100:0,previewNet=side==="buy"?previewMinor/100+previewFee:previewMinor/100-previewFee,previewValid=Number.isFinite(customerOrderAmount)&&customerOrderAmount>0&&customerOrderAmount<=1000000&&Math.abs(customerOrderAmount*100-previewMinor)<1e-7&&entryPrice>0&&Number.isFinite(previewQty)&&previewQty>0,previewFundsSufficient=side==="buy"?usd>=previewNet:assetBalance>=previewQty;

 async function auth(e){e.preventDefault();setAuthBusy(true);setAuthMessage("");try{const endpoint=authMode==="register"?"/auth/register":"/auth/login",r=await fetch(API+endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email.trim(),password})}),d=await r.json();if(!r.ok)throw Error(d.detail||"Authentication failed");if(authMode==="register"){setAuthMode("login");setPassword("");setAuthMessage("Account created. Please log in.");}else{localStorage.setItem("zencrypto_token",d.access_token);setToken(d.access_token);setPassword("");setAuthOpen(false);setNotice("Signed in successfully.");}}catch(e){setAuthMessage(e.message);}finally{setAuthBusy(false);}}
 async function logout(){try{if(token)await api("/auth/logout",{method:"POST"});}catch{}localStorage.removeItem("zencrypto_token");setToken("");setUser(null);setBalances([]);setOrders([]);}
 const nav=[["dashboard",LayoutDashboard,"Overview"],["trade",ChartCandlestick,"Markets"],["wallet",Wallet,"Account readiness"],["deposit",ArrowDownToLine,"Deposit status"],["withdraw",ArrowUpFromLine,"Withdrawal status"]];
 return <div className={"app notranslate "+(tab==="trade"?"trade-app":"")} translate="no">
 <aside className={mobile?"sidebar open":"sidebar"}><div className="brand"><div className="brandmark">Z</div><span>Zen<span className="accent">Crypto</span><small className="brand-subtitle">EXCHANGE</small></span></div><div className="navlabel">EXCHANGE</div><nav>{nav.map(([key,Icon,label])=><button key={key} className={tab===key?"nav active":"nav"} onClick={()=>{setTab(key);setMobile(false);}}><Icon size={19}/>{label}</button>)}</nav><div className="sidebarfoot"><span className="dot"/> PUBLIC MARKET DATA<p>Live customer order routing is disabled</p></div></aside>
 <main><header><button className="hamburger" onClick={()=>setMobile(!mobile)}><Menu size={23}/></button><div className="headerleft"><span className="breadcrumb">ZenCrypto / {nav.find(n=>n[0]===tab)?.[2]}</span><h1>{nav.find(n=>n[0]===tab)?.[2]}</h1></div><div className="headeractions"><span className="live"><span className="dot"/> MARKET DATA</span><button className="lang" onClick={()=>setLang(lang==="en"?"zh":"en")}><Globe size={16}/>{lang==="en"?"中文":"English"}</button>{token?<><span className="account-email">{user?.email||"Signed in"}</span><button className="auth-button auth-button-secondary" onClick={logout}>Logout</button></>:<button className="auth-button" onClick={()=>{setAuthMode("login");setAuthOpen(true);}}>Login / Register</button>}<div className="avatar">{user?.email?.[0]?.toUpperCase()||"Z"}</div></div></header>
 <div className="content">{error&&<div className="noticebox">{error}</div>}{notice&&<div className="noticebox">{notice}</div>}
 <>{/* HOME */}{tab==="dashboard"&&<><section className="zc-home-top"><div><div className="zc-eyebrow">ZENCRYPTO · DIGITAL ASSET MARKETS</div><h2 className="zc-home-title">Your market command center.</h2><p className="zc-home-copy">Follow live spot-market prices, compare liquidity and review market structure in one workspace. Execution access is shown separately from public market data.</p><div className="zc-home-actions"><button className="zc-btn zc-btn-primary" onClick={()=>setTab("trade")}>Explore markets →</button><button className="zc-btn zc-btn-secondary" onClick={()=>setTab("wallet")}>Account readiness</button></div></div><div className="zc-home-health"><span className={quoteFresh?"zc-health-dot online":"zc-health-dot"}></span><div><strong>{quoteFresh?"Public market feed live":tickerStatus==="reconnecting"?"Reconnecting":tickerStatus==="stale"?"Market feed delayed":"Connecting"}</strong><small>{tickerUpdatedAt?"Last update "+new Date(tickerUpdatedAt).toLocaleTimeString():"Waiting for verified ticker update"}</small><small>Public market data · no customer funds connected</small></div></div></section><section className="zc-home-status-grid"><div className="zc-home-status-card"><span className="zc-status-kicker">EXECUTION CONNECTOR</span><strong>{exchangeStatusLoading?"Checking…":exchangeStatusError?"Unavailable":exchangeStatus?.sandbox?"Sandbox configured":"Connector response received"}</strong><small>{exchangeStatusError?("Connector check: "+exchangeStatusError):exchangeStatus?.exchange?String(exchangeStatus.exchange).toUpperCase()+" Spot · "+(exchangeStatus.build_revision?String(exchangeStatus.build_revision).slice(0,8):"version not reported"):"Provider not confirmed"}</small></div><div className="zc-home-status-card"><span className="zc-status-kicker">REAL-MONEY ORDERS</span><strong className="zc-status-warning">Not enabled</strong><small>Per-user authorization, risk controls and compliance approval are required.</small></div><div className="zc-home-status-card"><span className="zc-status-kicker">PUBLIC MARKET DATA</span><strong className={quoteFresh?"zc-status-good":"zc-status-warning"}>{quoteFresh?"Live feed verified":"Waiting for feed"}</strong><small>Informational quotes, not executable prices.</small></div></section><div className="zc-home-section-head"><div><h2 className="zc-section-title">Market watch</h2><p className="muted">Live public spot pairs · select an asset to inspect its market</p></div><button className="tablebtn" onClick={()=>setTab("trade")}>Open market terminal →</button></div><div className="zc-market-strip">{markets.map(m=><button type="button" className="zc-market-card zc-market-card-button" key={m.symbol} onClick={()=>{setSymbol(m.symbol);setTab("trade");}}><div className="zc-market-symbol">{m.symbol}/USDT ↗</div><div className="zc-market-price">{quoteFresh&&m.price>0?"$"+fmt(m.price):"Waiting for quote"}</div><div className={quoteFresh?(m.change>=0?"zc-market-change zc-positive":"zc-market-change zc-negative"):"zc-market-change"}>{quoteFresh?(m.change>=0?"+":"")+fmt(m.change)+"%":"—"} <small>24h</small></div><div className="zc-market-extra"><span>Vol {quoteFresh?"$"+fmtCompact(m.quoteVolume):"—"}</span><span>H {quoteFresh?fmt(m.high):"—"} · L {quoteFresh?fmt(m.low):"—"}</span></div></button>)}</div><div className="zc-home-section-head"><div><h2 className="zc-section-title">All markets</h2><p className="muted">Search supported spot assets and open the selected market.</p></div><label className="zc-market-search"><span>⌕</span><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search symbol or asset"/></label></div><div className="panel marketpanel"><div className="tablewrap"><table><thead><tr><th>Asset</th><th>Last price</th><th>24h change</th><th>24h volume</th><th>24h range</th><th>Feed</th><th>Action</th></tr></thead><tbody>{filtered.map(m=><tr key={m.symbol}><td><strong>{m.symbol}</strong> · {m.name}</td><td>{quoteFresh&&m.price>0?"$"+fmt(m.price):"—"}</td><td className={quoteFresh?(m.change>=0?"green":"red"):"muted"}>{quoteFresh?(m.change>=0?"+":"")+fmt(m.change)+"%":"—"}</td><td>{quoteFresh?"$"+fmtCompact(m.quoteVolume):"—"}</td><td>{quoteFresh?fmt(m.high)+" / "+fmt(m.low):"—"}</td><td><span className={quoteFresh?"zc-table-live":"zc-table-stale"}>{quoteFresh?"LIVE":"WAITING"}</span></td><td><button type="button" className="tablebtn" onClick={()=>{setSymbol(m.symbol);setTab("trade");}}>View market →</button></td></tr>)}</tbody></table></div></div><p className="zc-home-footnote">Public market data only. ZenCrypto does not currently accept deposits, hold assets, or submit customer orders.</p></>}</>{tab==="trade"&&<div className="zc-binance-terminal">
<div className="zc-market-terminal-head"><div><div className="zc-eyebrow">MARKET TERMINAL</div><h2>{selected.symbol}/USDT <span className="muted">{selected.name}</span></h2><p className="muted">Public market data and market structure · order execution is not enabled</p></div><div className="zc-terminal-price"><strong>{quoteFresh&&selected.price>0?"$"+fmt(selected.price):"—"}</strong><span className={quoteFresh?(selected.change>=0?"green":"red"):"muted"}>{quoteFresh?(selected.change>=0?"+":"")+fmt(selected.change)+"%":"Waiting for quote"}</span></div></div>
<div className="zc-terminal-grid">
 <aside className="zc-terminal-orderbook"><div className="panel market-depth-panel"><div className="panelhead"><div><h3>External market depth · {symbol}/USDT</h3><p className="muted">Public spot order book · informational only</p></div><span className={orderBook?.synchronized?"depth-live":"depth-live syncing"}><i/> {orderBook?.synchronized?"LIVE":"SYNCING"}</span></div>{depthError&&<p className="depth-error">{depthError}</p>}{orderBook?.synchronized&&orderBook.bids?.length&&orderBook.asks?.length?(()=>{const bids=orderBook.bids.slice(0,12),asks=orderBook.asks.slice(0,12).reverse(),bestBid=Number(orderBook.bids[0][0]),bestAsk=Number(orderBook.asks[0][0]),spread=bestAsk-bestBid,mid=(bestAsk+bestBid)/2;return <><div className="depth-spread"><span>Spread <b>{fmt(spread,4)}</b></span><span>{mid?((spread/mid)*100).toFixed(3):"—"}%</span></div><div className="depth-columns"><div className="depth-side depth-asks"><div className="depth-head"><span>PRICE (USDT)</span><span>AMOUNT</span></div>{asks.map((r,i)=><div className="depth-row" key={"a"+i}><span>{fmt(r[0],4)}</span><span>{fmt(r[1],5)}</span></div>)}</div><div className="depth-side depth-bids"><div className="depth-head"><span>PRICE (USDT)</span><span>AMOUNT</span></div>{bids.map((r,i)=><div className="depth-row" key={"b"+i}><span>{fmt(r[0],4)}</span><span>{fmt(r[1],5)}</span></div>)}</div></div></>;})():<p className="empty">Waiting for synchronized public order-book data…</p>}<div className="recent-trades"><h3>Recent public trades</h3><div className="depth-head"><span>PRICE (USDT)</span><span>AMOUNT</span><span>TIME</span></div>{recentTrades.slice(0,12).map(x=><div className="recent-trade-row" key={x.id}><span className={x.isBuyerMaker?"red":"green"}>{fmt(x.price,4)}</span><span>{fmt(x.qty,5)}</span><span>{new Date(x.time).toLocaleTimeString()}</span></div>)}</div><p className="fine">Public market depth and trades only. No customer orders are submitted from this terminal.</p></div></aside>
 <section className="zc-terminal-mainchart"><div className="panel chartpanel zc-terminal-chart"><div className="panelhead"><div><h3>{selected.symbol}/USDT · Candlestick chart</h3><p className="muted">Historical OHLC · volume · technical studies</p></div><div className="periods">{[["1m","1m"],["5m","5m"],["15m","15m"],["30m","30m"],["1h","1h"],["4h","4h"],["1d","1d"],["1w","1w"]].map(([label,value])=><button type="button" key={value} className={timeframe===value?"selected":""} onClick={()=>setTimeframe(value)}>{label}</button>)}<button type="button" onClick={()=>setChartRefresh(v=>v+1)}>↻ Refresh</button></div></div>{chartLoading?<p className="empty">Loading historical market candles…</p>:chartError?<p className="empty chart-error">Market data unavailable: {chartError}</p>:<CandlestickChart key={symbol} candles={chart} timeframe={timeframe}/>}<p className="fine">Public market reference data. Chart prices are not guaranteed execution prices.</p></div></section>
 <aside className="zc-terminal-right"><div className="zc-terminal-pairs">{markets.map(m=><button type="button" key={m.symbol} className={symbol===m.symbol?"pair-chip active":"pair-chip"} onClick={()=>setSymbol(m.symbol)}>{m.symbol}/USDT <span className={quoteFresh?(m.change>=0?"green":"red"):"muted"}>{quoteFresh?(m.change>=0?"+":"")+fmt(m.change)+"%":"—"}</span></button>)}</div><div className="panel zc-order-preview-panel"><div className="panelhead"><div><h3>Customer order preflight</h3><p className="muted">Live Binance reference price and account balance · preview only</p></div><span className="backtest-tag">NO ORDER SUBMITTED</span></div><form className="zc-preview-form" onSubmit={requestOrderPreview}><div className="zc-side-control"><span>Side</span><div className="zc-side-switch"><button type="button" className={customerOrderSide==="buy"?"buy-active":""} onClick={()=>{setPreviewSide("buy");setPreviewResult(null);}}>Buy</button><button type="button" className={customerOrderSide==="sell"?"sell-active":""} onClick={()=>{setPreviewSide("sell");setPreviewResult(null);}}>Sell</button></div></div><div className="zc-order-type-control"><span>Order type</span><div className="zc-order-type-tabs"><button type="button" className={customerOrderType==="limit"?"active":""} onClick={()=>{setPreviewType("limit");setPreviewResult(null);}}>Limit</button><button type="button" className={customerOrderType==="market"?"active":""} onClick={()=>{setPreviewType("market");setPreviewResult(null);}}>Market</button></div></div><label>Amount ({symbol})<input type="number" min="0" step="any" value={orderPreviewQty} onChange={e=>{setOrderPreviewQty(e.target.value);setPreviewResult(null);}} required/></label>{customerOrderType==="limit"&&<label>Limit price (USDT)<input type="number" min="0" step="any" value={customerOrderPrice} onChange={e=>{setPreviewPrice(e.target.value);setPreviewResult(null);}} required/></label>}<button type="submit" className="zc-btn zc-btn-primary" disabled={customerOrderBusy||!EXCHANGE_API}>{customerOrderBusy?"Checking Binance…":"Calculate order preview"}</button></form>{!token&&<p className="fine">Sign in and connect Binance to use customer-specific preflight checks.</p>}{customerOrderError&&<div className="zc-account-message zc-preview-error">{customerOrderError}</div>}{customerOrderResult&&<div className="zc-preview-result"><div className="zc-preview-title"><strong>{customerOrderResult.side.toUpperCase()} {customerOrderResult.symbol}</strong><span className="zc-connection-pill">PREVIEW ONLY</span></div><div className="zc-preview-grid"><div><span>Estimated notional</span><b>{customerOrderResult.estimated_notional_usdt} USDT</b></div><div><span>Reference price</span><b>{customerOrderResult.reference_price} USDT</b></div><div><span>Fee safety buffer</span><b>{customerOrderResult.estimated_fee_buffer_usdt} USDT</b></div><div><span>Required {customerOrderResult.required_asset}</span><b>{customerOrderResult.estimated_required_balance}</b></div><div><span>Available {customerOrderResult.required_asset}</span><b>{customerOrderResult.available_balance}</b></div><div><span>Per-order cap</span><b>{customerOrderResult.per_order_preview_limit_usdt} USDT</b></div></div><p className="fine">Preflight passed against live read-only Binance market and balance data. Prices and balances can change before execution. ZenCrypto has not submitted or queued an order.</p><button type="button" className="zc-btn zc-btn-primary" onClick={submitCustomerOrder} disabled={customerOrderSubmitBusy||!exchangeConnection?.live_order_routing_enabled}>{customerOrderSubmitBusy?"Submitting to Binance…":exchangeConnection?.live_order_routing_enabled?"Confirm real Binance order":"Live order submission disabled"}</button>{customerOrderSubmitResult&&<p className="fine">Exchange accepted order {customerOrderSubmitResult.exchange_order_id} · {customerOrderSubmitResult.status}</p>}</div>}</div></aside>
</div>
<div className="zc-execution-gate"><div className="zc-gate-icon"><Shield size={22}/></div><div className="zc-gate-copy"><span className="zc-status-kicker">ORDER EXECUTION</span><h3>Real-money trading is not yet available</h3><p>{exchangeStatusLoading?"Checking exchange connector…":exchangeStatusError?"The exchange connector status could not be verified: "+exchangeStatusError:exchangeStatus?.sandbox?"The connector is configured for sandbox testing, not live customer orders.":"Market data connectivity is separate from customer order execution."} A secure per-user exchange authorization system, server-side credential vault, account-level permissions, risk checks, reconciliation and regulatory approval are required before live orders can be accepted.</p><div className="zc-gate-facts"><span>Public market data: <b className={quoteFresh?"zc-status-good":"zc-status-warning"}>{quoteFresh?"Connected":"Not verified"}</b></span><span>Live order routing: <b className="zc-status-warning">Disabled</b></span><span>Customer asset custody: <b className="zc-status-warning">Not provided</b></span></div></div></div>
<div className="zc-terminal-orders"><div className="panel zc-customer-order-history"><div className="panelhead"><div><h3>Open orders & order history</h3><p className="muted">Order status and fills synchronized from your connected account</p></div><button type="button" className="zc-btn zc-btn-secondary" onClick={loadCustomerOrders} disabled={customerOrdersLoading||!token}>{customerOrdersLoading?"Syncing…":"Refresh history"}</button></div>{!token?<p className="fine">Sign in to view your customer-specific order history.</p>:customerOrdersLoading&&!customerOrders.length?<p className="fine">Loading orders from ZenCrypto order journal…</p>:!customerOrders.length?<p className="fine">No submitted customer orders have been recorded yet.</p>:<div className="zc-order-history-list">{customerOrders.map((record)=>{const order=record.order||{};const id=order.exchange_order_id;const status=String(order.status||record.request_status||"unknown").toLowerCase();const terminal=["closed","canceled","cancelled","expired","rejected"].includes(status);return <div className="zc-order-history-row" key={record.idempotency_key}><div className="zc-order-history-main"><strong>{order.side?.toUpperCase()||"ORDER"} {order.symbol||"—"}</strong><span>{order.order_type||"—"} · {status.toUpperCase()} · {order.filled||"0"} / {order.amount||"—"} filled</span><small>Exchange order: {id||"Awaiting exchange confirmation"} · {order.cost||"0"} USDT</small></div>{id&&<div className="zc-order-history-actions"><button type="button" className="zc-btn zc-btn-secondary" onClick={()=>refreshCustomerOrder(id)} disabled={customerOrderActionId===id||!exchangeConnection?.connected}>{customerOrderActionId===id?"Working…":"Sync"}</button>{!terminal&&<button type="button" className="zc-btn zc-btn-danger" onClick={()=>cancelCustomerOrder(id)} disabled={customerOrderActionId===id||!exchangeConnection?.connected}>Cancel</button>}</div>}</div>;})}</div>}{customerOrderError&&<div className="zc-account-message zc-preview-error">{customerOrderError}</div>}</div></div>
</div>}{tab==="wallet"&&<div className="zc-account-page"><div className="zc-account-heading"><div><div className="zc-eyebrow">YOUR EXCHANGE ACCOUNTS</div><h2>Connect your Binance account</h2><p>Your assets stay on Binance. ZenCrypto stores an encrypted API authorization and does not request withdrawal permission.</p></div><span className="zc-account-provider">BINANCE SPOT</span></div>{!token?<div className="zc-account-panel"><h3>Sign in to connect an exchange</h3><p>Sign in to your ZenCrypto account first.</p><button type="button" className="zc-btn zc-btn-primary" onClick={()=>{setAuthMode("login");setAuthOpen(true);}}>Sign in / Create account</button></div>:<div className="zc-account-panel">{exchangeConnectionLoading?<p>Checking Binance connection…</p>:exchangeConnection?.connected?<><div className="zc-connection-row"><div><span className="zc-status-kicker">BINANCE CONNECTION</span><h3>Account connected</h3><p>API key {exchangeConnection.masked_api_key} · authorization verified</p></div><span className="zc-connection-pill connected">CONNECTED</span></div><div className="zc-permission-grid"><div><span>Account reading</span><b className="zc-status-good">Enabled</b></div><div><span>Spot trading permission</span><b className="zc-status-good">Verified</b></div><div><span>Withdrawals</span><b className="zc-status-good">Disabled</b></div><div><span>ZenCrypto order routing</span><b className="zc-status-warning">Not enabled</b></div></div><div className="zc-account-actions"><button type="button" className="zc-btn zc-btn-secondary" disabled={exchangeBalancesLoading} onClick={loadExchangeBalances}>{exchangeBalancesLoading?"Loading…":"Refresh Binance balances"}</button><button type="button" className="zc-btn zc-btn-danger" disabled={exchangeConnectBusy} onClick={disconnectBinance}>Disconnect account</button></div>{exchangeBalances.length>0&&<div className="zc-account-balances"><h3>Account balances</h3><div className="tablewrap"><table><thead><tr><th>Asset</th><th>Total</th><th>Available</th><th>In use</th></tr></thead><tbody>{exchangeBalances.map(row=><tr key={row.asset}><td><strong>{row.asset}</strong></td><td>{row.total}</td><td>{row.free}</td><td>{row.used}</td></tr>)}</tbody></table></div><p className="fine">Read directly from your connected Binance account. Assets remain on Binance.</p></div>}</>:<form className="zc-connect-form" onSubmit={connectBinance}><h3>Connect with a Binance API key</h3><p>Create a dedicated API key in Binance. Do not enter your Binance password or 2FA code.</p><label>API key<input type="password" autoComplete="off" spellCheck="false" value={exchangeApiKey} onChange={e=>setExchangeApiKey(e.target.value)} placeholder="Paste API key" minLength={16} maxLength={256} required/></label><label>API secret<input type="password" autoComplete="new-password" spellCheck="false" value={exchangeApiSecret} onChange={e=>setExchangeApiSecret(e.target.value)} placeholder="Paste API secret" minLength={16} maxLength={256} required/></label><div className="zc-key-safety"><strong>Binance key settings required</strong><ul><li>Enable account reading.</li><li>Enable Binance Spot &amp; Margin Trading API permission; ZenCrypto will call Spot endpoints only and will not use Margin or Futures endpoints.</li><li>Keep withdrawals disabled.</li><li>Use IP restrictions and whitelist the ZenCrypto server egress IP when configured.</li><li>Do not enable Futures or withdrawal permissions.</li></ul></div><button className="zc-btn zc-btn-primary" type="submit" disabled={exchangeConnectBusy||!EXCHANGE_API}>{exchangeConnectBusy?"Verifying and encrypting…":"Verify and securely connect"}</button>{!EXCHANGE_API&&<p className="zc-form-error">Exchange connector URL is not configured.</p>}</form>}{exchangeConnectMessage&&<div className="zc-account-message">{exchangeConnectMessage}</div>}</div>}<div className="zc-live-trading-lock"><Shield size={20}/><div><strong>Live order placement remains locked</strong><p>Account linking and balance reading are the first integration stage. Real customer orders require audited order controls, limits, reconciliation, incident response and legal/compliance approval.</p></div></div></div>}{(tab==="deposit"||tab==="withdraw")&&<div className="zc-wallet-page"><div className="zc-eyebrow">HYBRID ASSET ACCOUNTS</div><h2>{tab==="deposit"?"Deposit assets":"Withdraw assets"}</h2><p className="muted">ZenCrypto keeps your connected exchange account separate from your independent custody wallet. Exchange assets remain at your exchange; custody-wallet funds require a qualified third-party custodian.</p><div className="zc-wallet-grid"><section className="zc-wallet-card"><div className="zc-wallet-card-head"><strong>Connected exchange</strong><span className={exchangeConnection?.connected?"zc-status-good":"zc-status-warning"}>{exchangeConnection?.connected?"CONNECTED":"NOT CONNECTED"}</span></div><p>Binance balances are fetched directly from your own authorized exchange account.</p>{exchangeConnection?.connected?<><button type="button" className="zc-btn zc-btn-secondary" onClick={loadExchangeBalances} disabled={exchangeBalancesLoading}>{exchangeBalancesLoading?"Refreshing…":"Refresh exchange balances"}</button>{exchangeBalances.length>0?<div className="zc-wallet-assets">{exchangeBalances.map(row=><div key={row.asset}><b>{row.asset}</b><span>{row.free} available</span><small>{row.used} in use</small></div>)}</div>:<p className="fine">No non-zero balances loaded. Refresh to retrieve current account data.</p>}</>:<p className="fine">Connect Binance in Account settings to see your real exchange balances.</p>}</section><section className="zc-wallet-card"><div className="zc-wallet-card-head"><strong>Independent ZenCrypto wallet</strong><span className={walletSummary?.wallet_enabled?"zc-status-good":"zc-status-warning"}>{walletSummary?.wallet_enabled?"PROVIDER CONFIGURED":"NOT ACTIVATED"}</span></div><p>Separate customer wallet ledger backed by a qualified custody provider. No assets are represented until deposits are verified by the provider.</p>{walletLoading?<p className="fine">Loading wallet status…</p>:<><div className="zc-wallet-status"><span>Custody provider</span><b>{walletSummary?.custody_provider||"Not configured"}</b></div><div className="zc-wallet-status"><span>Ledger</span><b>Double-entry foundation</b></div><div className="zc-wallet-status"><span>Ledger integrity</span><b className={walletSummary?.ledger_integrity?.healthy?"zc-status-good":"zc-status-warning"}>{walletSummary?.ledger_integrity?.healthy?"Healthy":walletSummary?.ledger_integrity?.status==="review_required"?"Review required":"Not verified"}</b></div>{walletSummary?.ledger_integrity&&<p className="fine">{walletSummary.ledger_integrity.issue_count>0?walletSummary.ledger_integrity.issue_count+" ledger issue(s) require review.":"No ledger integrity issues detected."} · Checked {new Date(walletSummary.ledger_integrity.checked_at*1000).toLocaleString()}</p>}<div className="zc-wallet-assets">{(walletSummary?.balances||[]).map(row=><div key={row.asset}><b>{row.asset}</b><span>{row.available} available</span></div>)}</div><p className="fine">{walletSummary?.message||"Sign in to retrieve wallet status."}</p><div className="zc-wallet-activity"><strong>Wallet activity</strong>{(walletSummary?.activity||[]).length?walletSummary.activity.map(item=><div key={item.journal_id}><span>{item.event_type.replaceAll("_"," ")} · {item.asset}</span><b>{item.status}</b><small>{new Date(item.created_at*1000).toLocaleString()}</small></div>):<p className="fine">No verified custody transactions recorded.</p>}</div></>}</section></div><section className="zc-wallet-funding"><h3>{tab==="deposit"?"Deposit availability":"Withdrawal availability"}</h3><p>{tab==="deposit"?"Deposit addresses will only be issued by the approved custodian after network, asset and confirmation rules are configured. Never send assets to an address supplied outside the authenticated app.":"Withdrawals will require verified identity, MFA, destination allowlisting, risk screening and an auditable approval workflow."}</p><div className="zc-wallet-actions"><button type="button" className="zc-btn zc-btn-primary" disabled={!walletSummary?.deposits_enabled||tab!=="deposit"}> {walletSummary?.deposits_enabled?"Create deposit address":"Deposits not enabled"}</button><button type="button" className="zc-btn zc-btn-primary" disabled={!walletSummary?.withdrawals_enabled||tab!=="withdraw"}>{walletSummary?.withdrawals_enabled?"Request withdrawal":"Withdrawals not enabled"}</button><button type="button" className="zc-btn zc-btn-secondary" onClick={loadWalletSummary} disabled={walletLoading}>Refresh wallet status</button></div>{walletMessage&&<p className="zc-form-error">{walletMessage}</p>}</section><button type="button" className="zc-btn zc-btn-secondary" onClick={()=>setTab("dashboard")}>Back to market overview</button></div>}{tab==="admin"&&<div className="zc-portfolio-empty"><div className="zc-eyebrow">PLATFORM OPERATIONS</div><h2>Administrative console not provisioned</h2><p>Administrative access requires role-based server authorization, MFA and a separate audited operations interface.</p><button type="button" className="zc-btn zc-btn-secondary" onClick={()=>setTab("dashboard")}>Back to overview</button></div>}<footer>© 2026 ZenCrypto · Public market data · Customer order execution not enabled</footer></div></main>{authOpen&&<div className="auth-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)setAuthOpen(false);}}><form className="auth-dialog" onSubmit={auth}><button type="button" className="auth-close" aria-label="Close" onClick={()=>setAuthOpen(false)}>×</button><div className="zc-eyebrow">ZENCRYPTO ACCOUNT</div><h2>{authMode==="register"?"Create your account":"Welcome back"}</h2><p className="muted">{authMode==="register"?"Create an account before connecting your exchange.":"Sign in to manage your exchange connections."}</p><label>Email<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoComplete="username"/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required minLength={12} maxLength={128} autoComplete={authMode==="register"?"new-password":"current-password"}/></label>{authMessage&&<p className="zc-form-error">{authMessage}</p>}<button className="auth-button" type="submit" disabled={authBusy}>{authBusy?"Please wait…":authMode==="register"?"Create account":"Sign in"}</button><button type="button" className="auth-switch" onClick={()=>{setAuthMode(authMode==="register"?"login":"register");setAuthMessage("");}}>{authMode==="register"?"Already registered? Sign in":"New to ZenCrypto? Create account"}</button></form></div>}</div>;
}
class AppErrorBoundary extends React.Component {
 constructor(props){super(props);this.state={error:null};}
 static getDerivedStateFromError(error){return {error};}
 componentDidCatch(error,info){console.error("ZenCrypto render failure:",error,info);}
 render(){
  if(this.state.error)return <div className="boot-screen"><div className="boot-card"><h1>ZenCrypto could not start</h1><p>The interface encountered a rendering error. Your saved account session has not been changed.</p><pre className="boot-error">{String(this.state.error?.message||this.state.error)}</pre><button className="auth-button" onClick={()=>window.location.reload()}>Reload application</button></div></div>;
  return this.props.children;
 }
}
const rootElement=document.getElementById("root");
if(rootElement)createRoot(rootElement).render(<AppErrorBoundary><App/></AppErrorBoundary>);
else console.error("ZenCrypto root element #root was not found.");
