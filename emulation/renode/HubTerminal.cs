// Receive-only UART bridge: loopback binding, one bounded frame, no offline backlog.
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using Antmicro.Renode.Backends.Terminals;
using Antmicro.Renode.Core;

namespace Antmicro.Renode.Backends.Terminals
{
    public static class HubTerminalExtensions
    {
        public static void CreateHubTerminal(this Emulation emulation, int port, string name)
        { emulation.ExternalsManager.AddExternal(new HubTerminal(port), name); }
    }

    public class HubTerminal : BackendTerminal, IDisposable
    {
        public HubTerminal(int port)
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start(1);
            thread = new Thread(Accept) { IsBackground = true, Name = "Hub UART" };
            thread.Start();
        }
        public int Port { get { return ((IPEndPoint)listener.LocalEndpoint).Port; } }
        public long Connections { get; private set; }
        public long FramesSent { get; private set; }
        public long FramesDropped { get; private set; }
        public void Disconnect()
        {
            lock(sync) { if(client != null) client.Close(); client = null; }
        }
        public override void WriteChar(byte value)
        {
            // Discard an overlong line in full, then resume at the next boundary.
            if(used < frame.Length) frame[used++] = value; else overflow = true;
            if(value != 10) return;
            lock(sync)
            {
                if(!overflow && client != null)
                {
                    try { client.GetStream().Write(frame, 0, used); FramesSent++; }
                    catch(IOException) { Disconnect(); FramesDropped++; }
                    catch(ObjectDisposedException) { Disconnect(); FramesDropped++; }
                }
                else FramesDropped++;
            }
            used = 0; overflow = false;
        }
        public void Dispose()
        {
            stopped = true;
            listener.Stop();
            Disconnect();
            thread.Join();
        }
        private void Accept()
        {
            while(!stopped)
            {
                TcpClient accepted;
                try { accepted = listener.AcceptTcpClient(); }
                catch(SocketException) { break; }
                catch(ObjectDisposedException) { break; }
                lock(sync)
                {
                    if(stopped || client != null) { accepted.Close(); continue; }
                    accepted.NoDelay = true;
                    accepted.SendTimeout = 250;
                    client = accepted; Connections++;
                }
            }
        }
        private readonly TcpListener listener;
        private readonly Thread thread;
        private readonly object sync = new object();
        private readonly byte[] frame = new byte[2048];
        private TcpClient client;
        private int used;
        private bool overflow;
        private volatile bool stopped;
    }
}
