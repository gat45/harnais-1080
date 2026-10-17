using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Threading;

// Lance llama-server.exe (fork turboquant) + le harnais Python (RAG BM25 +
// outils portes de l'app Android + fallback skill git-ecosystem-investigator).
// Options en ligne de commande (toutes optionnelles, valeurs par defaut =
// recette README_RELEASE.md "qualite maximale - s3 + chain3") :
//
//   --model <chemin.gguf>     modele a charger (defaut : q38_9b_s3_gutop.gguf)
//   --ctx N                   taille de contexte (defaut : 2048)
//   --ngl N                   couches sur GPU (defaut : 99)
//   --threads N                (defaut : 12)
//   --no-mtp                  desactive le MTP chain3 (config AR simple)
//   --spec-chain N             profondeur de chaine MTP (defaut : 3)
//   --spec-n-max N             tokens draftes par etape (defaut : 3)
//   --reasoning-budget N       budget de tokens pour le <think> force par ce
//                              fork (defaut : 0 = fin immediate -- sans ca le
//                              modele peut partir en boucle de raisonnement
//                              et ne jamais atteindre la reponse ; teste :
//                              5.4s avec 0 contre un run coupe a 2048 tokens
//                              de think sans reponse sans ce flag)
//   --port N                  (defaut : 8080)
//   --server-exe <chemin>     binaire llama-server.exe a utiliser
//                              (defaut : fork turboquant, supporte draft-mtp)
//   --ask "question"          pose UNE question au harnais puis quitte
//                              (sinon : ouvre le harnais en mode conversation)
//   --reindex-rag             reconstruit bm25_index.json avant de lancer
//   --no-harness              lance seulement le serveur, pas harness.py
//   --skip-server             ne lance pas le serveur (deja demarre ailleurs),
//                              lance seulement le harnais
//   --fix-code <dossier>      lance fix_code.py (dual-agent plan+critique,
//                              porte de Dual-Agent_Code_Fixer_FIXED.py vers
//                              le serveur local) sur un dossier Python, puis
//                              quitte -- n'ouvre pas le harnais interactif
//   --monitor                 ouvre le TUI materiel (OMNI-SLA v6.4, fourni par
//                              l'utilisateur, 2 bugs corriges a la source :
//                              import importlib.util manquant + patterns
//                              dangereux verifies par ligne au lieu du fichier
//                              entier) dans une console separee, en plus ou a
//                              la place du harnais
//   --stop                    arrete tout llama-server.exe en cours puis quitte
//                              (evite l'incident deja vu : 2 instances sur le
//                              meme port)
//   --force                   si un serveur repond deja sur --port, le tue et
//                              relance quand meme (sans --force : reutilise le
//                              serveur deja present, ne duplique jamais)
//   --list-recipes            affiche les recettes connues (llama_launcher.json
//                              ['tuned']) puis quitte
//   --log <fichier>           chemin du log serveur (defaut : llama_server.log
//                              dans le dossier du harnais) -- avant : la sortie
//                              du serveur etait perdue a la fermeture de sa
//                              fenetre, aucune trace persistante
class LamaHarnessLauncher
{
    const string DefaultServerExe = @"E:\oneplus\geniex_harness\npu-rtx\reference\llama-cpp-turboquant\build-cu61\bin\llama-server.exe";
    const string DefaultModelPath = @"F:/archive_s3_release/model/q38_9b_s3_gutop.gguf";
    const string HarnessDir = @"E:\lama-tensorRT 1050-5070";

    static int Main(string[] args)
    {
        Dictionary<string, string> opt = new Dictionary<string, string>();
        List<string> flags = new List<string>();
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            if (a == "--no-mtp" || a == "--reindex-rag" || a == "--no-harness" || a == "--skip-server" || a == "--monitor"
                || a == "--stop" || a == "--force" || a == "--list-recipes")
            {
                flags.Add(a);
            }
            else if (a.StartsWith("--") && i + 1 < args.Length)
            {
                opt[a.Substring(2)] = args[++i];
            }
        }

        string serverExe = opt.ContainsKey("server-exe") ? opt["server-exe"] : DefaultServerExe;
        string modelPath = opt.ContainsKey("model") ? opt["model"] : DefaultModelPath;
        string ctx = opt.ContainsKey("ctx") ? opt["ctx"] : "2048";
        string ngl = opt.ContainsKey("ngl") ? opt["ngl"] : "99";
        string threads = opt.ContainsKey("threads") ? opt["threads"] : "12";
        string port = opt.ContainsKey("port") ? opt["port"] : "8080";
        string specChain = opt.ContainsKey("spec-chain") ? opt["spec-chain"] : "3";
        string specNMax = opt.ContainsKey("spec-n-max") ? opt["spec-n-max"] : "3";
        string reasoningBudget = opt.ContainsKey("reasoning-budget") ? opt["reasoning-budget"] : "0";
        bool noMtp = flags.Contains("--no-mtp");
        bool reindexRag = flags.Contains("--reindex-rag");
        bool noHarness = flags.Contains("--no-harness");
        bool skipServer = flags.Contains("--skip-server");
        bool force = flags.Contains("--force");
        string logFile = opt.ContainsKey("log") ? opt["log"] : Path.Combine(HarnessDir, "llama_server.log");
        string healthUrl = "http://127.0.0.1:" + port + "/health";

        if (flags.Contains("--stop"))
        {
            int killed = 0;
            foreach (Process pr in Process.GetProcessesByName("llama-server"))
            {
                try { pr.Kill(); killed++; } catch (Exception) { }
            }
            Console.WriteLine(killed + " instance(s) de llama-server.exe arretee(s).");
            return 0;
        }

        if (flags.Contains("--list-recipes"))
        {
            string launcherJson = Path.Combine(HarnessDir, "llama_launcher.json");
            if (!File.Exists(launcherJson))
            {
                Console.WriteLine("ERREUR : " + launcherJson + " introuvable.");
                return 1;
            }
            Console.WriteLine(File.ReadAllText(launcherJson));
            return 0;
        }

        if (reindexRag)
        {
            Console.WriteLine("[0/4] Reindexation RAG (bm25.py, chunking)...");
            RunAndWait("python", "bm25_reindex.py", HarnessDir);
        }

        int step = reindexRag ? 1 : 0;
        int total = (skipServer ? 0 : 1) + (noHarness ? 0 : 1) + step;
        int cur = step;

        if (!skipServer)
        {
            cur++;

            // MANQUE REEL CORRIGE (liste des manques, 2026-10-17) : rien ne
            // detectait un serveur deja vivant sur ce port avant d'en lancer
            // un second -- incident reel vecu cette session (2 llama-server.exe
            // sur le port 8080, un seul vraiment en LISTEN, l'autre dans un
            // etat residuel). Sans --force : reutilise le serveur existant.
            if (IsHealthy(healthUrl))
            {
                if (!force)
                {
                    Console.WriteLine(string.Format(
                        "[{0}/{1}] Un serveur repond deja sur {2} -- reutilise (passe --force pour relancer).",
                        cur, total, healthUrl));
                    goto AfterServer;
                }
                Console.WriteLine("--force : arret du serveur existant avant relance...");
                foreach (Process pr in Process.GetProcessesByName("llama-server"))
                {
                    try { pr.Kill(); } catch (Exception) { }
                }
                Thread.Sleep(1500);
            }

            if (!File.Exists(serverExe))
            {
                Console.WriteLine("ERREUR : llama-server.exe introuvable : " + serverExe);
                return 1;
            }
            string modelLocal = modelPath.Replace('/', '\\');
            if (!File.Exists(modelLocal))
            {
                Console.WriteLine("ERREUR : modele introuvable : " + modelLocal);
                return 1;
            }

            string specArgs = noMtp ? "" :
                string.Format(" --spec-type draft-mtp --spec-draft-n-max {0} --spec-chain {1}", specNMax, specChain);
            string serverArgs = string.Format(
                "-m \"{0}\" -ngl {1} -fa on -t {2} -c {3}{4} --reasoning-budget {5} --host 127.0.0.1 --port {6}",
                modelPath, ngl, threads, ctx, specArgs, reasoningBudget, port);

            Console.WriteLine(string.Format("[{0}/{1}] Lancement de llama-server ({2})...",
                cur, total, noMtp ? "AR simple" : "MTP chain" + specChain));
            Console.WriteLine("      " + serverArgs);
            Console.WriteLine("      log : " + logFile);

            // MANQUE REEL CORRIGE : UseShellExecute=true (avant) ouvrait le
            // serveur dans sa propre console -- visible, mais perdu a la
            // fermeture de cette fenetre, aucune trace persistante. On
            // redirige maintenant stdout/stderr vers un fichier ET la console
            // courante (le process garde le controle, rien n'est perdu).
            StreamWriter logWriter = new StreamWriter(logFile, false);
            logWriter.AutoFlush = true;

            ProcessStartInfo psi = new ProcessStartInfo(serverExe, serverArgs);
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.WorkingDirectory = Path.GetDirectoryName(serverExe);
            Process serverProc = new Process();
            serverProc.StartInfo = psi;
            serverProc.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                lock (logWriter) { logWriter.WriteLine(e.Data); }
            };
            serverProc.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                lock (logWriter) { logWriter.WriteLine(e.Data); }
            };
            serverProc.Start();
            serverProc.BeginOutputReadLine();
            serverProc.BeginErrorReadLine();
            Console.WriteLine("      pid " + serverProc.Id + " -- attente du health-check reel...");

            bool up = false;
            for (int i = 0; i < 60; i++)
            {
                if (IsHealthy(healthUrl)) { up = true; break; }
                Thread.Sleep(2000);
            }

            if (!up)
            {
                Console.WriteLine("ERREUR : le serveur n'a pas repondu sur " + healthUrl + " apres 120s. Voir " + logFile);
                return 1;
            }
            Console.WriteLine("      serveur pret (health = ok), pid " + serverProc.Id + ".");
        }
        AfterServer:

        if (opt.ContainsKey("fix-code"))
        {
            Console.WriteLine("[fix-code] Analyse+correction dual-agent sur " + opt["fix-code"] + "...");
            RunAndWait("python", "fix_code.py " + Quote(opt["fix-code"]) + " --yes", HarnessDir);
            Console.WriteLine("Termine.");
            return 0;
        }

        if (flags.Contains("--monitor"))
        {
            Console.WriteLine("[monitor] Ouverture du TUI materiel (OMNI-SLA, corrige)...");
            ProcessStartInfo mpsi = new ProcessStartInfo("cmd.exe", "/k python omni_monitor.py");
            mpsi.UseShellExecute = true;
            mpsi.WorkingDirectory = HarnessDir;
            Process.Start(mpsi);
        }

        if (!noHarness)
        {
            cur++;
            string harnessArgs = opt.ContainsKey("ask") ? ("harness.py " + Quote(opt["ask"])) : "harness.py";
            Console.WriteLine(string.Format("[{0}/{1}] Lancement du harnais (RAG BM25 + outils + skills)...", cur, total));

            if (opt.ContainsKey("ask"))
            {
                RunAndWait("python", harnessArgs, HarnessDir);
            }
            else
            {
                ProcessStartInfo hpsi = new ProcessStartInfo("cmd.exe", "/k python " + harnessArgs);
                hpsi.UseShellExecute = true;
                hpsi.WorkingDirectory = HarnessDir;
                Process.Start(hpsi);
            }
        }

        Console.WriteLine("Termine.");
        return 0;
    }

    static bool IsHealthy(string url)
    {
        try
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 2000;
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            {
                return resp.StatusCode == HttpStatusCode.OK;
            }
        }
        catch (WebException)
        {
            return false;
        }
    }

    static string Quote(string s)
    {
        return "\"" + s.Replace("\"", "\\\"") + "\"";
    }

    static void RunAndWait(string exe, string arguments, string workDir)
    {
        ProcessStartInfo psi = new ProcessStartInfo(exe, arguments);
        psi.UseShellExecute = false;
        psi.WorkingDirectory = workDir;
        Process p = Process.Start(psi);
        p.WaitForExit();
    }
}
