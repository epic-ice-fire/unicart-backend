import "package:flutter/material.dart";
import "../../services/auth_service.dart";
import "../../services/session_service.dart";
import "../lobby/lobby_screen.dart";
import "register_screen.dart";

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final emailController    = TextEditingController();
  final passwordController = TextEditingController();
  bool isLoading    = false;
  bool rememberMe   = true;
  bool showPassword = false;

  @override
  void initState() {
    super.initState();
    _loadSavedCredentials();
  }

  Future<void> _loadSavedCredentials() async {
    final savedEmail    = await SessionService.getSavedEmail();
    final savedPassword = await SessionService.getSavedPassword();
    if (!mounted) return;
    if (savedEmail != null && savedEmail.isNotEmpty) {
      setState(() {
        emailController.text = savedEmail;
        rememberMe = true;
      });
    }
    if (savedPassword != null && savedPassword.isNotEmpty) {
      setState(() => passwordController.text = savedPassword);
    }
  }

  @override
  void dispose() {
    emailController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  Future<void> login() async {
    final email    = emailController.text.trim();
    final password = passwordController.text.trim();

    if (email.isEmpty || password.isEmpty) {
      showMessage("Enter your email and password.");
      return;
    }

    setState(() => isLoading = true);

    try {
      final token = await AuthService.login(email: email, password: password);

      await SessionService.saveToken(token);
      if (rememberMe) {
        await SessionService.saveEmail(email);
        await SessionService.savePassword(password);
      } else {
        await SessionService.clearEmail();
        await SessionService.clearPassword();
      }

      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => LobbyScreen(token: token)),
      );
    } catch (e) {
      final raw = e.toString();
      String msg;
      if (raw.contains("401") || raw.contains("Invalid email or password")) {
        msg = "Incorrect email or password. Please try again.";
      } else if (raw.contains("Failed to fetch") ||
          raw.contains("SocketException") ||
          raw.contains("ClientException") ||
          raw.contains("Connection")) {
        msg = "Could not connect to server. Check your internet and try again.";
      } else if (raw.contains("500")) {
        msg = "Server is waking up — please try again in a moment.";
      } else {
        msg = "Login failed. Please try again.";
      }
      showMessage(msg);
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  void showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Row(children: [
        const Icon(Icons.error_outline, color: Colors.white, size: 18),
        const SizedBox(width: 10),
        Expanded(child: Text(message,
            style: const TextStyle(fontWeight: FontWeight.w600))),
      ]),
      backgroundColor: const Color(0xFFB42318),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      margin: const EdgeInsets.all(16),
      duration: const Duration(seconds: 4),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Image.asset(
                    "assets/images/logo.png",
                    height: 80,
                    width: 80,
                  ),
                  const SizedBox(height: 20),
                  const Text("Welcome back",
                      style: TextStyle(fontSize: 30, fontWeight: FontWeight.w800,
                          color: Color(0xFF101828))),
                  const SizedBox(height: 8),
                  const Text("Log in to continue your campus group buying.",
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 15, color: Color(0xFF667085))),
                  const SizedBox(height: 24),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(children: [
                        TextField(
                          controller: emailController,
                          keyboardType: TextInputType.emailAddress,
                          decoration: const InputDecoration(
                            labelText: "Email",
                            prefixIcon: Icon(Icons.mail_outline),
                          ),
                        ),
                        const SizedBox(height: 14),
                        TextField(
                          controller: passwordController,
                          obscureText: !showPassword,
                          decoration: InputDecoration(
                            labelText: "Password",
                            prefixIcon: const Icon(Icons.lock_outline),
                            suffixIcon: IconButton(
                              icon: Icon(showPassword
                                  ? Icons.visibility_off_outlined
                                  : Icons.visibility_outlined,
                                  size: 20, color: const Color(0xFF667085)),
                              onPressed: () =>
                                  setState(() => showPassword = !showPassword),
                            ),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Row(children: [
                          Checkbox(
                            value: rememberMe,
                            activeColor: const Color(0xFF1F7A4C),
                            shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(4)),
                            onChanged: (v) =>
                                setState(() => rememberMe = v ?? true),
                          ),
                          const Text("Remember me",
                              style: TextStyle(fontSize: 14,
                                  color: Color(0xFF344054),
                                  fontWeight: FontWeight.w500)),
                          const Spacer(),
                          Text("Stay logged in",
                              style: TextStyle(fontSize: 12,
                                  color: Colors.grey.shade400)),
                        ]),
                        const SizedBox(height: 10),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: isLoading ? null : login,
                            child: Text(isLoading ? "Logging in..." : "Login"),
                          ),
                        ),
                        const SizedBox(height: 10),
                        TextButton(
                          onPressed: isLoading ? null : () {
                            Navigator.push(context, MaterialPageRoute(
                                builder: (_) => const RegisterScreen()));
                          },
                          child: const Text("Create an account"),
                        ),
                      ]),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}