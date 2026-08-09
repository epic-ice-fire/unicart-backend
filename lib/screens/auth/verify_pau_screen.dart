import "package:flutter/material.dart";
import "../../services/auth_service.dart";

class VerifyPauScreen extends StatefulWidget {
  final String token;

  const VerifyPauScreen({super.key, required this.token});

  @override
  State<VerifyPauScreen> createState() => _VerifyPauScreenState();
}

class _VerifyPauScreenState extends State<VerifyPauScreen> {
  final pauEmailController = TextEditingController();
  final codeController = TextEditingController();
  bool isLoading = false;
  String? devCode;

  @override
  void dispose() {
    pauEmailController.dispose();
    codeController.dispose();
    super.dispose();
  }

  Future<void> requestCode() async {
    final pauEmail = pauEmailController.text.trim();

    if (pauEmail.isEmpty) {
      showMessage("Enter your PAU email.");
      return;
    }

    setState(() => isLoading = true);

    try {
      final response = await AuthService.requestPauCode(
        token: widget.token,
        pauEmail: pauEmail,
      );

      setState(() {
        devCode = response["dev_code"]?.toString();
      });

      showMessage("Verification code requested.");
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) {
        setState(() => isLoading = false);
      }
    }
  }

  Future<void> verifyCode() async {
    final code = codeController.text.trim();

    if (code.isEmpty) {
      showMessage("Enter the verification code.");
      return;
    }

    setState(() => isLoading = true);

    try {
      await AuthService.verifyPauCode(
        token: widget.token,
        code: code,
      );

      showMessage("PAU email verified successfully.");

      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) {
        setState(() => isLoading = false);
      }
    }
  }

  void showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Verify PAU email")),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: ListView(
                shrinkWrap: true,
                children: [
                  const Text(
                    "Student verification",
                    style: TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.w800,
                      color: Color(0xFF101828),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    "Link your PAU email so you can join lobbies and add items.",
                    style: TextStyle(
                      fontSize: 15,
                      color: Color(0xFF667085),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        children: [
                          TextField(
                            controller: pauEmailController,
                            keyboardType: TextInputType.emailAddress,
                            decoration: const InputDecoration(
                              labelText: "PAU email",
                              prefixIcon: Icon(Icons.school_outlined),
                            ),
                          ),
                          const SizedBox(height: 14),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton(
                              onPressed: isLoading ? null : requestCode,
                              child: const Text("Request verification code"),
                            ),
                          ),
                          if (devCode != null) ...[
                            const SizedBox(height: 14),
                            Container(
                              width: double.infinity,
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: const Color(0xFFF2F4F7),
                                borderRadius: BorderRadius.circular(16),
                              ),
                              child: SelectableText(
                                "Dev code: $devCode",
                                style: const TextStyle(
                                  fontSize: 16,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        children: [
                          TextField(
                            controller: codeController,
                            decoration: const InputDecoration(
                              labelText: "Verification code",
                              prefixIcon: Icon(Icons.password_outlined),
                            ),
                          ),
                          const SizedBox(height: 14),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton(
                              onPressed: isLoading ? null : verifyCode,
                              child: const Text("Verify code"),
                            ),
                          ),
                        ],
                      ),
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