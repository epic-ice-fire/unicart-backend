class Lobby {
  final int lobbyId;
  final String status;
  final int currentItemAmount;
  final int targetItemAmount;
  final int memberCount;

  Lobby({
    required this.lobbyId,
    required this.status,
    required this.currentItemAmount,
    required this.targetItemAmount,
    required this.memberCount,
  });

  factory Lobby.fromJson(Map<String, dynamic> json) {
    return Lobby(
      lobbyId: (json["lobby_id"] as num?)?.toInt() ?? 0,
      status: json["status"]?.toString() ?? "open",
      currentItemAmount: (json["current_item_amount"] as num?)?.toInt() ?? 0,
      targetItemAmount: (json["target_item_amount"] as num?)?.toInt() ?? 0,
      memberCount: (json["member_count"] as num?)?.toInt() ?? 0,
    );
  }
}