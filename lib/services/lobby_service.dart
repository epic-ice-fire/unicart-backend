import "dart:convert";
import "package:http/http.dart" as http;
import "../config/api.dart";

class LobbyService {
  static String get baseUrl => ApiConfig.baseUrl;

  static Map<String, String> _headers(String token) {
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer $token",
    };
  }

  static dynamic _decodeResponse(http.Response response) {
    if (response.body.isEmpty) return null;
    return jsonDecode(response.body);
  }

  static Exception _errorFromResponse(http.Response response) {
    try {
      final data = _decodeResponse(response);
      final detail = data is Map<String, dynamic>
          ? (data["detail"] ?? data["message"] ?? "Request failed.")
          : "Request failed.";
      return Exception(detail.toString());
    } catch (_) {
      return Exception("Request failed with status ${response.statusCode}");
    }
  }

  // GET /lobbies/main/details
  static Future<Map<String, dynamic>> mainLobbyDetails(String token) async {
    final response = await http.get(
      Uri.parse("$baseUrl/lobbies/main/details"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // GET /lobbies/main/my-items
  static Future<Map<String, dynamic>> myMainLobbyItems(String token) async {
    final response = await http.get(
      Uri.parse("$baseUrl/lobbies/main/my-items"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // GET /lobbies/my-history
  static Future<Map<String, dynamic>> myBatchHistory(String token) async {
    final response = await http.get(
      Uri.parse("$baseUrl/lobbies/my-history"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /payments/entry-fee/initialize
  static Future<Map<String, dynamic>> initializeEntryFeePayment(
      String token) async {
    final response = await http.post(
      Uri.parse("$baseUrl/payments/entry-fee/initialize"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // GET /payments/verify/{reference}
  static Future<Map<String, dynamic>> verifyEntryFeePayment(
    String token, {
    required String reference,
  }) async {
    final response = await http.get(
      Uri.parse("$baseUrl/payments/verify/$reference"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /lobbies/main/leave
  static Future<Map<String, dynamic>> leaveLobby(String token) async {
    final response = await http.post(
      Uri.parse("$baseUrl/lobbies/main/leave"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /lobbies/main/items
  static Future<Map<String, dynamic>> addItem(
    String token, {
    required String itemLink,
    required int itemAmount,
  }) async {
    final response = await http.post(
      Uri.parse("$baseUrl/lobbies/main/items"),
      headers: _headers(token),
      body: jsonEncode({"item_link": itemLink, "item_amount": itemAmount}),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /lobbies/main/items/{item_id}/remove
  static Future<Map<String, dynamic>> removeItem(
    String token, {
    required int itemId,
  }) async {
    final response = await http.post(
      Uri.parse("$baseUrl/lobbies/main/items/$itemId/remove"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /lobbies/admin/items/{item_id}/remove  ← Admin force-remove
  static Future<Map<String, dynamic>> adminForceRemoveItem(
    String token, {
    required int itemId,
  }) async {
    final response = await http.post(
      Uri.parse("$baseUrl/lobbies/admin/items/$itemId/remove"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // POST /payments/items/{item_id}/initialize
  static Future<Map<String, dynamic>> initializeItemPayment(
    String token, {
    required int itemId,
  }) async {
    final response = await http.post(
      Uri.parse("$baseUrl/payments/items/$itemId/initialize"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // GET /payments/items/verify/{reference}
  static Future<Map<String, dynamic>> verifyItemPayment(
    String token, {
    required String reference,
  }) async {
    final response = await http.get(
      Uri.parse("$baseUrl/payments/items/verify/$reference"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // GET /lobbies/admin/dashboard
  static Future<Map<String, dynamic>> adminDashboard(String token) async {
    final response = await http.get(
      Uri.parse("$baseUrl/lobbies/admin/dashboard"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // PATCH /lobbies/admin/open-lobby/target
  static Future<Map<String, dynamic>> adminUpdateOpenLobbyTarget(
    String token, {
    required int targetItemAmount,
  }) async {
    final response = await http.patch(
      Uri.parse("$baseUrl/lobbies/admin/open-lobby/target"),
      headers: _headers(token),
      body: jsonEncode({"target_item_amount": targetItemAmount}),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }

  // PATCH /lobbies/admin/batches/{lobby_id}/status
  static Future<Map<String, dynamic>> adminUpdateBatchStatus(
    String token, {
    required int lobbyId,
    required String newStatus,
  }) async {
    final response = await http.patch(
      Uri.parse(
          "$baseUrl/lobbies/admin/batches/$lobbyId/status?new_status=$newStatus"),
      headers: _headers(token),
    );
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return _decodeResponse(response) as Map<String, dynamic>;
    }
    throw _errorFromResponse(response);
  }
}