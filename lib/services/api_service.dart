import "dart:convert";
import "package:http/http.dart" as http;
import "../config/api.dart";

class ApiService {
  static Future<Map<String, dynamic>> get(
    String endpoint, {
    String? token,
  }) async {
    final response = await http.get(
      Uri.parse("${ApiConfig.baseUrl}$endpoint"),
      headers: {
        "Accept": "application/json",
        if (token != null) "Authorization": "Bearer $token",
      },
    );

    return _handleResponse("GET", endpoint, response);
  }

  static Future<Map<String, dynamic>> post(
    String endpoint, {
    Map<String, dynamic>? body,
    String? token,
    bool formEncoded = false,
  }) async {
    final response = await http.post(
      Uri.parse("${ApiConfig.baseUrl}$endpoint"),
      headers: formEncoded
          ? {
              "Accept": "application/json",
              "Content-Type": "application/x-www-form-urlencoded",
              if (token != null) "Authorization": "Bearer $token",
            }
          : {
              "Accept": "application/json",
              "Content-Type": "application/json",
              if (token != null) "Authorization": "Bearer $token",
            },
      body: formEncoded ? body : jsonEncode(body ?? {}),
    );

    return _handleResponse("POST", endpoint, response);
  }

  static Future<Map<String, dynamic>> patch(
    String endpoint, {
    Map<String, dynamic>? body,
    String? token,
  }) async {
    final response = await http.patch(
      Uri.parse("${ApiConfig.baseUrl}$endpoint"),
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        if (token != null) "Authorization": "Bearer $token",
      },
      body: jsonEncode(body ?? {}),
    );

    return _handleResponse("PATCH", endpoint, response);
  }

  static Map<String, dynamic> _handleResponse(
    String method,
    String endpoint,
    http.Response response,
  ) {
    if (response.statusCode >= 200 && response.statusCode < 300) {
      if (response.body.isEmpty) {
        return {};
      }
      return jsonDecode(response.body) as Map<String, dynamic>;
    }

    throw Exception(
      "$method $endpoint failed: ${response.statusCode} ${response.body}",
    );
  }
}