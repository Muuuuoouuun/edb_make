using System;
using System.IO;
using System.Text;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Diagnostics;
using System.Collections.Generic;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;
class LocalApp {
  static TcpListener listener;
  static string address, host, route;
  static readonly Dictionary<string, byte[]> files = new Dictionary<string, byte[]>();
  static readonly Dictionary<string, string> mime = new Dictionary<string, string>();
  static readonly Semaphore limit = new Semaphore(8,8);
  const string CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'";
  static void Load() {
    var assembly = typeof(LocalApp).Assembly;
    foreach (var n in new [] {"index.html","app.js","core.js","names.js","demo.js","style.css"}) {
      var type = n.EndsWith(".html") ? "text/html" : (n.EndsWith(".css") ? "text/css" : "text/javascript");
      using (var raw = assembly.GetManifestResourceStream(n)) {
        if (raw==null)throw new InvalidOperationException("Missing embedded app resource");
        using(var copy=new MemoryStream()){raw.CopyTo(copy);files[n]=copy.ToArray();}
      }
      mime[n] = type+"; charset=utf-8";
    }
    files[""] = files["index.html"]; mime[""] = mime["index.html"];
  }
  static void Start() {
    Load();
    var bytes = new byte[16]; using(var rng = RandomNumberGenerator.Create())rng.GetBytes(bytes);
    route = "/" + BitConverter.ToString(bytes).Replace("-","").ToLowerInvariant()+"/";
    listener = new TcpListener(IPAddress.Loopback,0);listener.Start(12);
    host="127.0.0.1:"+((IPEndPoint)listener.LocalEndpoint).Port;
    address="http://"+host+route;
    var t = new Thread(Accept);t.IsBackground=true;t.Start();
  }
  static void Accept() {
    while(true) {
      TcpClient c;
      try { c=listener.AcceptTcpClient(); } catch(SocketException) { break; } catch(ObjectDisposedException) { break; }
      if (!limit.WaitOne(0)) { c.Close(); continue; }
      ThreadPool.QueueUserWorkItem(_=>{try{ Serve(c); } catch(IOException){} catch(SocketException){} finally{ c.Close(); limit.Release(); }});
    }
  }
  static byte[] Header(NetworkStream s) {
    byte[] data = new byte[8192];int i=0;int tail=0;int b;
    var timer=Stopwatch.StartNew();
    while(i<data.Length && timer.ElapsedMilliseconds<3000 && (b=s.ReadByte())!=-1) {
      data[i++]=(byte)b;tail=unchecked((tail<<8)|b);
      if(tail==0x0d0a0d0a){Array.Resize(ref data,i);return data;}
    }
    return null;
  }
  static void Serve(TcpClient c) {
    using(var s=c.GetStream()) {
      s.ReadTimeout=1200;s.WriteTimeout=2500;
      var h=Header(s); if(h==null) return;
      string[] lines=Encoding.ASCII.GetString(h).Split(new []{"\r\n"},StringSplitOptions.None);
      string[] req=lines[0].Split(' ');string key= null;string chosenHost = null;int hosts=0;
      for(int i=1;i<lines.Length;i++){
        if(lines[i].StartsWith("Host:",StringComparison.OrdinalIgnoreCase)){hosts++;chosenHost=lines[i].Substring(5).Trim();}
      }
      if(req.Length==3 && req[0]=="GET" && (req[2]=="HTTP/1.1"||req[2]=="HTTP/1.0") && hosts==1 && chosenHost==host && req[1].StartsWith(route,StringComparison.Ordinal))key=req[1].Substring(route.Length);
      byte[] body;
      if(key==null || !files.TryGetValue(key,out body)){Send(s,"404 Not Found","text/plain; charset=utf-8",Encoding.UTF8.GetBytes("Not found"));return;}
      Send(s,"200 OK",mime[key],body);
    }
  }
  static void Send(NetworkStream s, string status, string type, byte[] body) {
    var response=Encoding.ASCII.GetBytes("HTTP/1.1 "+status+"\r\nContent-Type: "+type+"\r\nContent-Length: "+body.Length+
      "\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nCross-Origin-Resource-Policy: same-origin\r\nContent-Security-Policy: "+CSP+"\r\n\r\n");
    s.Write(response,0,response.Length);s.Write(body,0,body.Length);
  }
  static void Open() {
    try {Process.Start(new ProcessStartInfo(address){ UseShellExecute=true });}
    catch(Exception){MessageBox.Show("기본 브라우저를 열 수 없습니다. 이 창의 주소를 복사해 Edge 또는 Chrome에 붙여넣으세요.","PNG Maker");}
  }
  [STAThread] static int Main(string[] args) {
    try {
      Start();
      if(args.Length==1&&args[0]=="--self-test") {
        using(var w=new WebClient()) {
          w.Proxy=null;
          if(!w.DownloadString(address).Contains("PNG Maker"))return 2;
          foreach(var n in new [] {"index.html","app.js","core.js","names.js","demo.js","style.css"}) {
            var body=w.DownloadData(address+n);
            if(!System.Linq.Enumerable.SequenceEqual(body,files[n]))return 3;
            if(w.ResponseHeaders["Cache-Control"]!="no-store" || w.ResponseHeaders["Content-Security-Policy"]!=CSP)return 4;
          }
          return 0;
        }
      }
      Application.EnableVisualStyles();
      var form=new Form(){Text="PNG Maker – 실행 중",ClientSize=new Size(420,173),FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,StartPosition=FormStartPosition.CenterScreen,AutoScaleMode=AutoScaleMode.Dpi};
      var header=new Label(){Text="PNG Maker · 로컬 편집기",Font=new Font("Malgun Gothic",13,FontStyle.Bold),AutoSize=true,Location=new Point(15,12)};
      var desc=new Label(){Text="편집하는 동안 이 창을 열어 두세요.\n파일은 이 컴퓨터 밖으로 전송되지 않습니다.",AutoSize=true,Location=new Point(16,46),Font=new Font("Malgun Gothic",9)};
      var url=new TextBox(){Text=address,Location=new Point(16,90),Width=388,ReadOnly=true,AccessibleName="로컬 브라우저 주소"};
      var button=new Button(){Text="편집기 열기",Location=new Point(15,125),Width=112,Height=29};button.Click+=(a,b)=>Open();
      var close=new Button(){Text="종료",Location=new Point(331,125),Width=74,Height=29};close.Click+=(a,b)=>form.Close();
      form.Controls.AddRange(new Control[]{header,desc,url,button,close});form.Shown+=(a,b)=>Open();Application.Run(form);return 0;
    } catch(Exception) {
      if(args.Length==1&&args[0]=="--self-test")return 1;
      MessageBox.Show("PNG Maker를 시작할 수 없습니다. ZIP 전체를 푼 뒤 PNG Maker.exe를 다시 실행하세요.","PNG Maker");return 1;
    } finally {if(listener!=null)listener.Stop();}
  }
}
