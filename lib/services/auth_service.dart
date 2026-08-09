import "api_service.dart";

class AuthService {
  static Future<Map<String, dynamic>> register({
    required String email,
    required String password,
  }) async {
    return await ApiService.post(
      "/auth/register",
      body: {
        "email": email,
        "password": password,
      },
    );
  }

  static Future<String> login({
    required String email,
    required String password,
  }) async {
    final response = await ApiService.post(
      "/auth/login",
      formEncoded: true,
      body: {
        "username": email,
        "password": password,
      },
    );

    return response["access_token"] as String;
  }

  static Future<Map<String, dynamic>> me(String token) async {
    return await ApiService.get("/auth/me", token: token);
  }

  static Future<Map<String, dynamic>> requestPauCode({
    required String token,
    required String pauEmail,
  }) async {
    return await ApiService.post(
      "/auth/pau/request",
      token: token,
      body: {
        "pau_email": pauEmail,
      },
    );
  }

  static Future<Map<String, dynamic>> verifyPauCode({
    required String token,
    required String code,
  }) async {
    return await ApiService.post(
      "/auth/pau/verify",
      token: token,
      body: {
        "code": code,
      },
    );
  }
}