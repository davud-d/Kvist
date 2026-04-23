package com.devagent.intellij.ui

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.notification.Notifications
import com.intellij.openapi.project.Project

object DevAgentNotifications {
    private const val GROUP_ID = "DevAgent Notifications"

    fun error(project: Project?, content: String) {
        val notification = NotificationGroupManager.getInstance()
            .getNotificationGroup(GROUP_ID)
            .createNotification(content, NotificationType.ERROR)

        if (project != null) {
            Notifications.Bus.notify(notification, project)
        }
        else {
            Notifications.Bus.notify(notification)
        }
    }
}