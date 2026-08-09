import "package:flutter/material.dart";
import "screens/auth/login_screen.dart";
import "screens/lobby/lobby_screen.dart";
import "services/session_service.dart";

void main() {
  runApp(const UniCartApp());
}

class UniCartApp extends StatelessWidget {
  const UniCartApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: "UniCart",
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1F7A4C),
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF6F8F7),
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          elevation: 0,
          backgroundColor: Colors.transparent,
          foregroundColor: Color(0xFF101828),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 16,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: const BorderSide(color: Color(0xFFD0D5DD)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: const BorderSide(color: Color(0xFFD0D5DD)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide:
                const BorderSide(color: Color(0xFF1F7A4C), width: 1.4),
          ),
        ),
        cardTheme: CardThemeData(
          color: Colors.white,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(24),
            side: const BorderSide(color: Color(0xFFE4E7EC)),
          ),
        ),
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            minimumSize: const Size.fromHeight(54),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
            ),
          ),
        ),
        outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
            minimumSize: const Size.fromHeight(54),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
            ),
          ),
        ),
      ),
      home: const AuthGate(),
    );
  }
}

class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  Future<String?> loadToken() async {
    return await SessionService.getToken();
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<String?>(
      future: loadToken(),
      builder: (context, snapshot) {
        // Show loading spinner while reading saved token
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Scaffold(
            body: Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  CircularProgressIndicator(color: Color(0xFF1F7A4C)),
                  SizedBox(height: 16),
                  Text("Loading UniCart...",
                      style: TextStyle(
                          color: Color(0xFF667085),
                          fontSize: 14,
                          fontWeight: FontWeight.w500)),
                ],
              ),
            ),
          );
        }

        final token = snapshot.data;

        // No token — show login screen
        if (token == null || token.isEmpty) {
          return const LoginScreen();
        }

        // Token exists — go straight to lobby.
        // The lobby screen handles the "Connecting..." state internally
        // if the backend is still waking up. No need to verify token here
        // because: 1) it avoids an extra network call on startup,
        // 2) the lobby loadAll() will handle auth errors gracefully.
        return LobbyScreen(token: token);
      },
    );
  }
}